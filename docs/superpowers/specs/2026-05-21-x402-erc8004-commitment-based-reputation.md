# x402 × ERC-8004 — Settlement-Payer Reputation Proof

**Date:** 2026-05-22 (v3 — settlement-payer model)  
**Status:** Spec  
**Implementation:** Python + Solidity (no TypeScript)

## Supersedes

`docs/superpowers/specs/2026-05-18-x402-erc8004-reputation-design.md` (agent-signature proof model)  
`docs/superpowers/specs/2026-05-21-x402-erc8004-commitment-based-reputation.md` (reqHash commitment model — replaced 2026-05-22)

Also supersedes the narrative in `docs/x402-erc8004-implementation.md` (signature + `X-Reputation-Proof` demo) once implemented.

---

## 1. Problem

Current flow requires the agent server to sign a proof (`X-Reputation-Proof` header) and return it to the client. If the agent server censors — doesn't return the header, doesn't forward settlement — the client cannot submit on-chain reputation feedback. The facilitator's `POST /feedback` endpoint is another centralization point.

Additionally, the agent server may settle directly (no facilitator). The proof mechanism must work in both cases.

**Goal:** Client can independently verify settlement + submit feedback directly on-chain, without requiring the agent server to cooperate after payment.

---

## 2. Solution: Settlement-Payer Proof

After settlement, the facilitator (or agent for direct settlement) records **who paid** for that settlement on-chain via `settlementPayer[txHash] = payer`. The client submits feedback via EIP-7702; the contract checks `settlementPayer[txHash] == msg.sender` for the **verified path** (with `usedSettlements[txHash]` dedup). If no settlement binding exists, feedback still goes through on the **unverified path** (dedup only, no settlement binding).

No `reqHash`, `respHash`, canonical JSON hashing, or commitment preimage is needed. The ERC-8004 `giveFeedback` interface already accepts all necessary parameters natively.

### Proof Model

| Claim | How Verified |
|-------|-------------|
| Client paid for service | `settlementPayer[txHash] == msg.sender` (via EIP-7702) |
| One settlement → one verified feedback | `usedSettlements[txHash]` |
| Feedback is unique | `consumeInteractionHash(feedbackHash)` |
| Client identity | `msg.sender` via EIP-7702 delegation → `ReputationRegistry` records client EOA |

### Why `settlementPayer` instead of a hash commitment

The previous model used `commitment = keccak256(agentId, reqHash, settlementTxHash)` as a shared secret — only the client (who received `reqHash` in the 402) could reconstruct it. This required:
- `reqHash` in 402 `erc-8004` extension
- Canonical JSON hashing (cross-stack serialization)
- Client computing the preimage to verify

The `settlementPayer` model uses **identity** instead of a **secret**:
- The person who paid is the only one who can submit verified feedback
- No hashing, no extensions, no serialization
- The contract verifies `msg.sender == payer` directly

This is simpler, more intuitive ("I paid, therefore I rate"), and eliminates the reqHash/respHash infrastructure entirely.

### Why `reqHash` / `respHash` are not needed

The ERC-8004 `IReputationRegistry.giveFeedback()` interface accepts:
- `agentId`, `value`, `valueDecimals`, `tag1`, `tag2`, `endpoint`, `feedbackURI`, `feedbackHash`

It does **not** accept `reqHash` or `respHash`. These were only in our spec as proposed struct additions. The feedback content (what request, what response, what rating) is either passed via the existing string parameters or encoded in `feedbackHash` for dedup. For this project, `feedbackHash = keccak256(abi.encode(agentId, txHash))` is sufficient — one settlement = one unique feedback.

### Censorship Resistance — Three Tiers

| Who records `settlementPayer[txHash]` | When | Outcome |
|--------|------|---------|
| **Facilitator** | During settlement (normal case) | Verified feedback |
| **Agent** | After seeing settlement on-chain | Verified feedback (agent wants ratings) |
| **No one** | Both unavailable | Unverified feedback — still counts via dedup, lower weight |

`recordSettlement` is permissionless: anyone can call it. If the payer is wrong, the real payer's `msg.sender` won't match, so no harm. If correct, only the real payer benefits.

---

## 3. Flow

```
Step 1  Client → Agent Server           GET /weather
                                          HTTP 402 + X-Payment-Requirements

Step 2  Client → Facilitator            POST /verify (signed EIP-3009 / Permit2)

Step 3  Client → Agent Server           GET /weather + PAYMENT-SIGNATURE header

Step 4  Agent Server → Facilitator      POST /settle
                                          → on-chain transferWithAuthorization / Permit2
                                          → settlementTxHash returned

Step 5  Agent Server → Client           200 OK + PAYMENT-RESPONSE header (txHash)

Step 6  Facilitator (or Agent)           FeedbackGateway.recordSettlement(txHash, payer)
                                          → settlementPayer[txHash] = payer

Step 7  Client                          Poll settlementPayer[txHash] until ≠ address(0)
                                          Sign EIP-7702 authorization → FeedbackGateway
                                          Send tx: from=client, to=client,
                                            authorizationList=[auth],
                                            data=submitFeedback(registry, params, txHash)
                                          Contract checks:
                                            settlementPayer[txHash] == msg.sender
                                              → verified path (usedSettlements[txHash] = true)
                                            else → unverified path (dedup only)
                                          Calls ReputationRegistry.giveFeedback(...)
                                              → client EOA recorded as feedback author
```

---

## 4. On-Chain State

### FeedbackGateway.sol

**New state:**
```solidity
mapping(bytes32 => address) public settlementPayer;  // txHash → payer
mapping(bytes32 => bool) public usedSettlements;      // txHash → consumed (verified path)
```

**Events:**
```solidity
event SettlementRecorded(bytes32 indexed txHash, address indexed payer);
```

**`recordSettlement`:**
```solidity
function recordSettlement(bytes32 txHash, address payer) external {
    if (settlementPayer[txHash] == address(0)) {
        settlementPayer[txHash] = payer;
        emit SettlementRecorded(txHash, payer);
    }
    // First-writer semantics: subsequent calls are no-ops
}
```

**`submitFeedback`:**
```solidity
function submitFeedback(
    address registry,
    FeedbackParams calldata params,
    bytes32 settlementTxHash
) external {
    if (settlementTxHash != bytes32(0) && settlementPayer[settlementTxHash] == msg.sender) {
        require(!usedSettlements[settlementTxHash], "Settlement already used");
        usedSettlements[settlementTxHash] = true;
    }

    IFeedbackGateway(dedupStore).consumeInteractionHash(params.feedbackHash);
    IReputationRegistry(registry).giveFeedback(
        params.agentId,
        params.value,
        params.valueDecimals,
        params.tag1,
        params.tag2,
        params.endpoint,
        params.feedbackURI,
        params.feedbackHash
    );
}
```

### Behaviors

| Condition | Result |
|-----------|--------|
| `settlementPayer[txHash] == msg.sender` × not reused | Verified; `usedSettlements[txHash]` set |
| `settlementPayer[txHash] == msg.sender` × already reused | Revert |
| `settlementTxHash == bytes32(0)` | Unverified; dedup only |
| `settlementPayer[txHash] != msg.sender` | Unverified; dedup only |
| Duplicate `feedbackHash` | Revert |

### ReputationRegistry

No changes.

---

## 5. What Changed from the Commitment Hash Model

| Dimension | Old (reqHash commitment) | New (settlement-payer) |
|-----------|--------------------------|----------------------|
| Trust mechanism | Shared secret (`reqHash`) | Identity (`msg.sender == payer`) |
| reqHash in 402 | Required in `erc-8004` extension | Not needed |
| respHash | Required for settlement binding | Not needed |
| Canonical JSON hashing | Required across Python + Solidity | Not needed |
| Commitment preimage | `keccak256(agentId, reqHash, txHash)` | Not used |
| Client reconstruction | 3-value hash | Just pass `txHash` |
| HTTP headers | `X-Reputation-*` headers | None needed |
| Test vectors | Complex hash vectors | None |
| Unverified attack vector | Observer can't find commitment key | Anyone can see txHash but `msg.sender` check prevents abuse |
| Censorship resistance | reqHash in 402 is single point of censorship | Three-tier (facilitator / agent / unverified) |

---

## 6. Solidity Changes

File: `naive_implementation/contracts/FeedbackGateway.sol`

- Remove: `commitments` mapping (v2 approach)
- Add: `settlementPayer` mapping
- Add: `usedSettlements` mapping
- Add: `recordSettlement(bytes32 txHash, address payer)` function
- Add: `SettlementRecorded` event
- Modify: `submitFeedback` — accept `bytes32 settlementTxHash` param, add payer check + settlement dedup before existing dedup/giveFeedback logic
- No changes to `FeedbackParams` struct

## 7. Foundry Tests

File: `naive_implementation/test/FeedbackGateway.t.sol`

- `test_recordSettlement_stores_and_emits`
- `test_recordSettlement_first_writer_wins` — second call with different payer is no-op
- `test_submitFeedback_verified_passes` — settlementPayer matches msg.sender
- `test_submitFeedback_reverts_when_settlement_reused`
- `test_submitFeedback_unverified_when_wrong_payer` — msg.sender ≠ settlmentPayer → unverified
- `test_submitFeedback_unverified_when_no_txHash` — txHash = bytes32(0) → dedup only
- `test_submitFeedback_delegated_EOA_uses_global_dedup` — update with optional txHash param

## 8. Python Implementation

### Process separation

Refactor `naive_implementation/main.py` into:

```
naive_implementation/
  pyproject.toml
  src/
    setup.py
    facilitator/app.py          # wrap POST /settle → recordSettlement
    agent_server/app.py         # serves /weather, no reputation logic needed
    client/app.py               # poll settlementPayer, EIP-7702 submitFeedback
    shared/
      constants.py
  contracts/FeedbackGateway.sol
  test/FeedbackGateway.t.sol
  test/conftest.py
  test/test_e2e.py
  Makefile
```

Remove: `_sign_proof`, `_verify_proof`, `_proof_hash`, signature-based `feedbackHash`, ReputationMiddleware, reqHash extensions.

### Facilitator (`src/facilitator/app.py`)

- `POST /verify`, `POST /settle` — x402 ExactEvm (unchanged)
- **After successful settle:**
  - Extract `settlementTxHash` from settle response
  - Extract `payer` from settlement context (client address)
  - `recordSettlement(txHash, payer)` via web3.py
- No reputation-related endpoints

### Agent Server (`src/agent_server/app.py`)

- `GET /weather` — serves weather report (unchanged)
- PaymentMiddlewareASGI — x402 payment enforcement (unchanged)
- **No ReputationMiddleware** — no reqHash, no reputation headers

### Client (`src/client/app.py`)

1. Pay via x402 HTTP transport (unchanged)
2. Extract `settlementTxHash` from `PAYMENT-RESPONSE` header
3. Poll `FeedbackGateway.settlementPayer[txHash]` until `!= address(0)`
4. Compute `feedbackHash = Web3.solidity_keccak(["uint256", "bytes32"], [agentId, settlementTxHash])`
5. Sign EIP-7702 authorization delegating to FeedbackGateway
6. Send self-call tx: `from: client, to: client, authorizationList: [auth], data: submitFeedback(registry, params, txHash)`
7. Assert `hasBeenUsed(feedbackHash) == true`

### Startup (`Makefile`)

- `make start` — launches Anvil, facilitator, agent server
- `make stop` — kills all processes
- `python -m src.setup` — one-time bootstrap (deploy contracts, register agent, fund client)

## 9. E2E Test Plan

1. Anvil mainnet fork; deploy gateway; register agent; fund client
2. Facilitator + agent server; client pays `GET /weather`
3. After settle: `settlementPayer[txHash] == client.address`
4. `submitFeedback` verified; `hasBeenUsed(feedbackHash)`; `usedSettlements[txHash] == true`
5. Same `txHash` with different `feedbackHash` → `usedSettlements` revert
6. Duplicate `feedbackHash` → `DuplicateHash` revert
7. Unverified: no settlement binding, dedup only
8. Direct-settlement mode: agent records settlement; client verifies without facilitator

## 10. Open Questions

1. **Permissioned `recordSettlement`:** v2 — restrict to facilitator + agent whitelist (currently permissionless, which is fine for v1 since wrong payer is harmless)
2. **Multi-chain:** Out of scope; per-chain deployments
3. **Unverified path weight:** How to distinguish verified vs unverified feedback in ReputationRegistry? v1: both go through same `giveFeedback` call. v2 could add a flag or separate contract.
