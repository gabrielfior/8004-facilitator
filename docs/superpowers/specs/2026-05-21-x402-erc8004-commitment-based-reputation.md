# x402 × ERC-8004 — Commitment-Based Reputation Proof

**Date:** 2026-05-21  
**Status:** Spec  
**Implementation:** Python + Solidity (no TypeScript)

## Supersedes

`docs/superpowers/specs/2026-05-18-x402-erc8004-reputation-design.md` (agent-signature proof model)

Also supersedes the narrative in `docs/x402-erc8004-implementation.md` (signature + `X-Reputation-Proof` demo) once implemented.

---

## 1. Problem

Current flow requires the agent server to sign a proof (`X-Reputation-Proof` header) and return it to the client. If the agent server censors — doesn't return the header, doesn't forward settlement — the client cannot submit on-chain reputation feedback. The facilitator's `POST /feedback` endpoint is another centralization point.

Additionally, the agent server may settle directly (no facilitator). The proof mechanism must work in both cases.

**Goal:** Client can independently verify settlement + submit feedback directly on-chain, without requiring the agent server to cooperate after payment.

---

## 2. Solution: Commitment-Based Proof

The agent includes a `reqHash` in the 402 response. After settlement, the facilitator (or agent, for direct settlement) records a **payment commitment** on-chain. After the paid 200 response, the agent records a **response hash** for that settlement. The client verifies on-chain state before submitting feedback; HTTP headers mirror the same fields for convenience (chain is authoritative).

### Proof Model

| Claim | How Verified |
|-------|-------------|
| Agent committed to request R for settlement T | `commitments[agentId][paymentCommitment] == true` where `paymentCommitment = keccak256(abi.encode(agentId, reqHash, settlementTxHash))` |
| Settlement T is bound to exactly one reqHash | `settlementRequestHash[settlementTxHash] == reqHash`; **revert** if the same tx hash is ever associated with a different `reqHash` |
| Settlement T is bound to exactly one response | `settlementResponseHash[settlementTxHash] == respHash`; **revert** on conflicting `recordResponseHash` |
| Client paid | Settlement transaction on-chain (`settlementTxHash`) |
| One settlement = one verified feedback | `usedSettlements[settlementTxHash]` on verified path |
| Feedback is unique | `usedHashes[feedbackHash]` dedup on FeedbackGateway |
| Response content (verified path) | `params.responseHash` must match `settlementResponseHash[settlementTxHash]` |

### Key Properties

- **Payment commitment** is recorded at settlement (agent/facilitator know `reqHash` + tx hash; they do not know rating or final response body yet).
- **Response hash** is recorded when the agent returns 200 (canonical payload hash — see §2.1).
- **On-chain state is authoritative.** HTTP headers duplicate the same `bytes32` values for client ergonomics; if headers are missing or wrong, the client recomputes from payloads + settle metadata and reads chain (Option A).

### Why tx hash is part of the payment commitment

Without it, one settlement could produce unlimited feedbacks (client fabricates different `respHash` / `rating` combos). Binding `paymentCommitment` to `settlementTxHash` plus `usedSettlements` enforces at most one verified feedback per settlement.

### 2.1 Canonical hashing (not raw HTTP / JSON bytes)

Do **not** hash raw response bytes or `json.dumps` output directly — whitespace, key order, and float serialization differ across stacks.

**Rule:** Hash a deterministic **canonical payload** per direction, then use `bytes32` values everywhere (402 extensions, headers, Solidity, Python).

#### Request hash (`reqHash`)

| Request body | Canonicalization | `reqHash` |
|--------------|------------------|-----------|
| Empty (e.g. `GET`) | Fixed empty input | `keccak256("")` — empty UTF-8 string, same as `bytes32` hash of zero-length ABI `bytes` in app code |
| JSON object | `json.loads` → `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` → UTF-8 | `keccak256(canonical_utf8)` |
| Non-JSON | Raw body bytes as received | `keccak256(body)` (document content-type on route) |

#### Response hash (`respHash`)

For JSON APIs (e.g. `/weather`):

1. Parse response body as JSON.
2. Canonicalize with the same `sort_keys` + compact separators as above.
3. `respHash = keccak256(canonical_utf8)`.

Implementations **must** share one helper (e.g. `shared/reputation_hash.py`: `canonical_json_bytes(obj) -> bytes`, `hash_request(body: bytes, content_type) -> bytes32`, `hash_response(body: bytes, content_type) -> bytes32`).

#### Test vectors

Golden vectors lock the §2.1 rules. **`naive_implementation/test/test_reputation_hash.py`** (and Foundry helpers if added) **must** assert these values; CI fails on drift.

**Reference (Python 3.11+, Web3.py `keccak`):**

```python
import json
from web3 import Web3

def canonical_json_bytes(obj: object) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")

def keccak32(data: bytes) -> bytes:
    return Web3.keccak(data)

def req_hash(body: bytes, content_type: str | None) -> bytes:
    if not body:
        return keccak32(b"")
    if content_type and "json" in content_type.split(";")[0].strip().lower():
        return keccak32(canonical_json_bytes(json.loads(body.decode("utf-8"))))
    return keccak32(body)

def resp_hash(body: bytes, content_type: str = "application/json") -> bytes:
    return keccak32(canonical_json_bytes(json.loads(body.decode("utf-8"))))
```

**Pinned edge cases (same principle as above — no format change):**

| Rule | Behavior |
|------|----------|
| `Content-Type` | Use canonical JSON path only when type is `application/json` or ends with `+json` |
| Root | Parsed JSON root must be an object for reputation routes |
| Numbers | Demo payloads use integers only (e.g. `72`, not `72.0`) |
| Duplicate keys | `json.loads` semantics (last wins) — avoid duplicate keys on the wire |
| Non-finite | Reject `NaN` / `Infinity` before hashing |

**`reqHash` / `respHash` vectors**

| ID | Input | Canonical UTF-8 (if JSON) | Expected hash |
|----|--------|---------------------------|---------------|
| `req-empty-get` | `GET /weather`, zero-length body | _(none — hash empty byte string)_ | `0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470` |
| `resp-weather-v1` | Parsed object `{"report":{"weather":"sunny","temperature":72}}` | `{"report":{"temperature":72,"weather":"sunny"}}` | `0x0522111b19cc4c1b09e4182c920276c5e4e76f52dc05f28ee32c6ba314af7392` |
| `json-empty-object` | `{}` | `{}` | `0xb48d38f93eaa084033fc5970bf96e559c33c4cdc07d889ab00b4d63f9590739d` |
| `json-unicode` | `{"msg":"café"}` | `{"msg":"café"}` | `0xe713118b47ba4dbcfd924d8e4d0e078c8291370a5c128ad5c2da9fc144559aca` |

**Anti-patterns (must not match `resp-weather-v1`):** hashing wire bytes without parse+canonicalize:

| Wire body | Hash (wrong) |
|-----------|----------------|
| `json.dumps(..., indent=2)` pretty-printed weather JSON | `0xac8c6be2d0ae54cac77191d50f009cd7e35fd14422ff4ce52d9e432f8997dbe6` |
| `json.dumps(...)` default compact (key order not sorted) | `0x51a4797d6694aa630f13af52e12a3a6c03349a169ca6c63c54d3e6879a1a806d` |

**Composite vectors (Solidity `abi.encode` / `solidity_keccak`)**

Use the `reqHash` / `respHash` bytes32 values from the table above.

| ID | Definition | Example inputs | Expected hash |
|----|------------|----------------|-----------------|
| `feedback-weather-v1` | `keccak256(abi.encode(agentId, reqHash, respHash, value, valueDecimals))` | `agentId=1`, `req-empty-get`, `resp-weather-v1`, `value=95`, `valueDecimals=0` | `0x5de2f404d63361c9a803534e3e6f50fe13722937912296fc5c0b09d5eea9cce8` |
| `payment-commitment-v1` | `keccak256(abi.encode(agentId, reqHash, settlementTxHash))` | `agentId=1`, `req-empty-get`, `settlementTxHash=0x00000000000000000000000000000000000000000000000000000000abc123` | `0x83a25b302f2b2b6609f960fe7cf6284612b66f874938ee05f76d49f67120c907` |

Settlement-dependent vectors (`payment-commitment-v1`, live E2E) use the real tx hash from the settle response; the row above is only for unit tests with a fixed `bytes32` placeholder.

#### Payment commitment

```solidity
paymentCommitment = keccak256(abi.encode(agentId, reqHash, settlementTxHash));
```

`settlementTxHash` is `bytes32` — normalize from settle response (32-byte tx hash, `0x`-prefixed hex stripped to `bytes32`).

#### Feedback identity hash

```solidity
feedbackHash = keccak256(abi.encode(agentId, reqHash, respHash, value, valueDecimals));
```

Use the same integer types as `FeedbackParams.value` / `valueDecimals` so Python `solidity_keccak` matches Solidity `abi.encode`.

### 2.2 HTTP headers — Option A (mirror chain; chain wins)

After settlement + 200 response, the agent SHOULD return headers that mirror what the client will verify on-chain:

| Header | Value | When |
|--------|-------|------|
| `X-Reputation-Agent-Id` | decimal `agentId` | 200 |
| `X-Reputation-Request-Hash` | `reqHash` (`0x` + 64 hex) | 402 (extension) and 200 |
| `X-Reputation-Response-Hash` | `respHash` | 200 |
| `X-Reputation-Settlement-Tx-Hash` | `settlementTxHash` | 200 (if known to agent from payment context) |
| `X-Reputation-Payment-Commitment` | `paymentCommitment` | 200 (if agent knows `settlementTxHash`) |

**Client algorithm:**

1. Compute `reqHash`, `respHash`, `paymentCommitment` locally from canonical rules + settle response.
2. If headers present, `require(header == local)` for each field; mismatch → treat as censored / untrusted HTTP.
3. Read `commitments[agentId][paymentCommitment]`, `settlementRequestHash[tx]`, `settlementResponseHash[tx]` from chain (poll until recorded if needed).
4. Submit feedback via EIP-7702.

If headers are absent, steps 3–4 still work using only local hashes and chain reads.

---

## 3. Flow

```
Step 1  Client → Agent Server           GET /weather

Step 2  Agent → Client                   HTTP 402 + PaymentRequired
                                         erc-8004 extension includes:
                                           reqHash (canonical), registerAuth, ...

Step 3  Client → Facilitator (or Agent)  POST /settle (ExactEvmScheme, USDC/DAI)
                                          PaymentPayload.extensions["erc-8004"].reqHash

Step 4  Settlement on-chain              transferWithAuthorization / Permit2
                                          → settlementTxHash in settle response

Step 5  Payment commitment on-chain      Facilitator (or agent):
                                           paymentCommitment = keccak256(agentId, reqHash, settlementTxHash)
                                         recordCommitment(agentId, reqHash, settlementTxHash)
                                           → sets commitments[...] and settlementRequestHash[tx]

Step 6  Client → Agent Server            Original request + PAYMENT-SIGNATURE

Step 7  Agent → Client                   200 + canonical JSON body
                                         recordResponseHash(settlementTxHash, respHash) on-chain
                                         Mirror headers (§2.2): reqHash, respHash, tx, paymentCommitment

Step 8  Client                           1. Canonical reqHash / respHash; settlementTxHash from settle
                                         2. Optionally verify X-Reputation-* headers == local
                                         3. Read commitments + settlementRequestHash + settlementResponseHash
                                         4. feedbackHash = keccak256(agentId, reqHash, respHash, value, valueDecimals)
                                         5. EIP-7702 submitFeedback(registry, params)
```

**Order:** Client must obtain `settlementTxHash` from the settle step before calling `submitFeedback` (today's `main.py` submits feedback before reading settlement — fix in implementation).

---

## 4. Who Writes On-Chain State

| Scenario | `recordCommitment` | `recordResponseHash` | When |
|----------|-------------------|----------------------|------|
| Agent uses facilitator | Facilitator after `POST /settle` | Agent on 200 | Sync after settle; sync after response |
| Agent settles directly | Agent after settle confirms | Agent on 200 | Same |
| Agent omits `reqHash` in 402 / extensions | Skipped | — | Unverified feedback only (dedup) |
| Agent refuses `recordResponseHash` | Payment commitment may exist | Missing | Verified `submitFeedback` **reverts** (response not bound) |

**No client address needed** for commitments — keyed by `agentId`, `reqHash`, `settlementTxHash`. Facilitator resolves `agentId` from `payTo` → agent store (see §11).

### Censorship Resistance

| Attack | Outcome |
|--------|---------|
| Agent omits `reqHash` from 402 | Facilitator cannot record payment commitment. Client: unverified path only. |
| Facilitator records commitment; agent censors headers | Client uses local canonical hashes + chain. |
| Agent records payment commitment but not `recordResponseHash` | Verified feedback **reverts** until response hash is on-chain (client may poll). |
| Facilitator/agent tries two reqHashes for same tx | `recordCommitment` **reverts** (`SettlementRequestMismatch`). |
| Agent tries two respHashes for same tx | `recordResponseHash` **reverts** (`SettlementResponseMismatch`). |

**Unverified path (v1):** If `commitments[agentId][paymentCommitment]` is false, `submitFeedback` still runs with dedup only — no `usedSettlements` / response binding. Documented threat: omitting `reqHash` allows unlimited unverified feedback per payment. Acceptable for demo; tighten in v2 if needed.

---

## 5. Solidity Changes

### 5.1 FeedbackGateway.sol

File: `naive_implementation/contracts/FeedbackGateway.sol`

**New state:**

```solidity
mapping(uint256 => mapping(bytes32 => bool)) public commitments;
mapping(bytes32 => bytes32) public settlementRequestHash;  // settlementTxHash => reqHash
mapping(bytes32 => bytes32) public settlementResponseHash; // settlementTxHash => respHash
mapping(bytes32 => bool) public usedSettlements;
```

**New errors:**

```solidity
error SettlementRequestMismatch(bytes32 settlementTxHash, bytes32 existingReqHash, bytes32 newReqHash);
error SettlementResponseMismatch(bytes32 settlementTxHash, bytes32 existingRespHash, bytes32 newRespHash);
error SettlementNotIndexed(bytes32 settlementTxHash);
error ResponseHashNotRecorded(bytes32 settlementTxHash);
error RequestHashMismatch(bytes32 settlementTxHash);
error ResponseHashMismatch(bytes32 settlementTxHash);
```

**Events:**

```solidity
event CommitmentRecorded(
    uint256 indexed agentId,
    bytes32 indexed paymentCommitment,
    bytes32 indexed settlementTxHash,
    bytes32 reqHash
);
event ResponseHashRecorded(bytes32 indexed settlementTxHash, bytes32 respHash);
```

**`recordCommitment`:**

```solidity
function recordCommitment(
    uint256 agentId,
    bytes32 requestHash,
    bytes32 settlementTxHash
) external {
    bytes32 existingReq = settlementRequestHash[settlementTxHash];
    if (existingReq != bytes32(0) && existingReq != requestHash) {
        revert SettlementRequestMismatch(settlementTxHash, existingReq, requestHash);
    }
    settlementRequestHash[settlementTxHash] = requestHash;

    bytes32 paymentCommitment = keccak256(abi.encode(agentId, requestHash, settlementTxHash));
    commitments[agentId][paymentCommitment] = true;
    emit CommitmentRecorded(agentId, paymentCommitment, settlementTxHash, requestHash);
}
```

Permissionless for v1: wrong `(agentId, reqHash, tx)` only sets slots honest clients never use.

**`recordResponseHash`:**

```solidity
function recordResponseHash(bytes32 settlementTxHash, bytes32 responseHash) external {
    bytes32 existing = settlementResponseHash[settlementTxHash];
    if (existing != bytes32(0) && existing != responseHash) {
        revert SettlementResponseMismatch(settlementTxHash, existing, responseHash);
    }
    settlementResponseHash[settlementTxHash] = responseHash;
    emit ResponseHashRecorded(settlementTxHash, responseHash);
}
```

**`FeedbackParams`:**

```solidity
struct FeedbackParams {
    uint256 agentId;
    int128 value;
    uint8 valueDecimals;
    string tag1;
    string tag2;
    string endpoint;
    string feedbackURI;
    bytes32 feedbackHash;
    bytes32 requestHash;
    bytes32 responseHash;
    bytes32 settlementTxHash;
}
```

**`submitFeedback`:**

```solidity
function submitFeedback(address registry, FeedbackParams calldata params) external {
    bytes32 paymentCommitment = keccak256(
        abi.encode(params.agentId, params.requestHash, params.settlementTxHash)
    );

    if (commitments[params.agentId][paymentCommitment]) {
        bytes32 boundReq = settlementRequestHash[params.settlementTxHash];
        if (boundReq == bytes32(0)) revert SettlementNotIndexed(params.settlementTxHash);
        if (boundReq != params.requestHash) revert RequestHashMismatch(params.settlementTxHash);

        bytes32 boundResp = settlementResponseHash[params.settlementTxHash];
        if (boundResp == bytes32(0)) revert ResponseHashNotRecorded(params.settlementTxHash);
        if (boundResp != params.responseHash) revert ResponseHashMismatch(params.settlementTxHash);

        require(!usedSettlements[params.settlementTxHash], "Settlement already used");
        usedSettlements[params.settlementTxHash] = true;
    }
    // else: unverified — dedup only

    IFeedbackGateway(dedupStore).consumeInteractionHash(params.feedbackHash);
    IReputationRegistry(registry).giveFeedback(
        params.agentId, params.value, params.valueDecimals,
        params.tag1, params.tag2, params.endpoint,
        params.feedbackURI, params.feedbackHash
    );
}
```

**Key behaviors:**

| Condition | Result |
|-----------|--------|
| Payment commitment exists; req/resp match bindings; tx unused | Verified; `usedSettlements[tx]` set |
| Payment commitment exists; tx already used | Revert |
| Payment commitment exists; `params.requestHash` ≠ `settlementRequestHash[tx]` | Revert |
| Payment commitment exists; `params.responseHash` ≠ `settlementResponseHash[tx]` | Revert |
| Payment commitment exists; response hash never recorded | Revert |
| No payment commitment | Unverified; dedup only |
| Duplicate `feedbackHash` | Revert |

### 5.2 ReputationRegistry

No changes.

### 5.3 Foundry Tests

File: `naive_implementation/test/FeedbackGateway.t.sol`

- `test_recordCommitment_stores_and_emits`
- `test_recordCommitment_reverts_when_tx_bound_to_different_reqHash`
- `test_recordResponseHash_stores_and_reverts_on_conflict`
- `test_submitFeedback_verified_passes` — full bindings + `usedSettlements`
- `test_submitFeedback_reverts_when_settlement_reused`
- `test_submitFeedback_reverts_when_request_hash_mismatch` — commitment recorded for reqA; submit with reqB + same tx
- `test_submitFeedback_reverts_when_response_hash_mismatch`
- `test_submitFeedback_reverts_when_response_not_recorded` — commitment without `recordResponseHash`
- `test_submitFeedback_unverified_passes` — no commitment
- Update `test_submitFeedback_delegated_EOA_uses_global_dedup` — add hash fields
- Use §2.1 **Test vectors** (`feedback-weather-v1`, `payment-commitment-v1`) for `bytes32` constants in Solidity tests

**Python:** `test/test_reputation_hash.py` asserts all §2.1 golden rows (required before E2E).

---

## 6. Python Implementation

### 6.1 Process separation

Refactor `naive_implementation/main.py` into:

```
naive_implementation/
  pyproject.toml
  src/
    setup.py
    facilitator/app.py          # wrap POST /settle → recordCommitment
    agent_server/app.py         # reqHash on 402, recordResponseHash + headers on 200
    client/app.py               # canonical hashes, header check, chain poll, submitFeedback
    shared/
      constants.py
      reputation_hash.py        # canonical JSON + keccak helpers (§2.1)
  contracts/FeedbackGateway.sol
  test/FeedbackGateway.t.sol
  test/conftest.py
  test/test_e2e.py
  Makefile
```

Remove: `_sign_proof`, `_verify_proof`, `_proof_hash`, signature-based `feedbackHash`.

### 6.2 Shared setup (`src/setup.py`)

Unchanged intent: Anvil, deploy gateway, register agent, fund client, print addresses. Persist `agentId` and `payTo → agentId` for facilitator (file or Redis-like JSON for demo).

### 6.3 Facilitator (`src/facilitator/app.py`)

- `POST /verify`, `POST /settle` — x402 ExactEvm
- **After successful settle (synchronous):**
  - `reqHash` from `payment_payload.extensions["erc-8004"].reqHash`
  - `settlementTxHash` from settle response (normalize to `bytes32`)
  - `agentId` from `payTo` → store
  - `recordCommitment(agentId, reqHash, settlementTxHash)` (facilitator wallet pays gas)
- No `POST /feedback`
- No `GET /reputation` in naive demo (optional; exists in repo root TypeScript facilitator only)

Python has no `onAfterSettle` today — implement as **post-settle logic in the FastAPI handler**, not a separate hook name requirement.

### 6.4 Agent server (`src/agent_server/app.py`)

- **`reqHash` on 402:** Use `ResourceServerExtension.enrich_declaration` (or middleware that mutates 402) — see §11. Do not rely on outer `ReputationMiddleware` that only handles 200.
- **On 200:** `respHash = hash_response(body)`; read `settlementTxHash` from payment/settle context; `recordResponseHash(tx, respHash)`; set headers per §2.2.
- **Direct settlement mode:** same `recordCommitment` after local settle; env flag to skip `HTTPFacilitatorClient`.

**Middleware caution:** `await request.body()` consumes the stream — use SDK hooks or careful re-injection so POST payment routes keep working.

### 6.5 Client (`src/client/app.py`)

1. Canonical `reqHash` / `respHash` (§2.1).
2. `settlementTxHash` from `x402HTTPClient.get_payment_settle_response` **before** feedback.
3. Optional header equality check (§2.2).
4. Poll `commitments`, `settlementRequestHash`, `settlementResponseHash`.
5. `feedbackHash` via `abi.encode` types matching contract.
6. EIP-7702 `submitFeedback` **directly** — client signs authorization, sends tx to self (`from: client, to: client, authorizationList: [auth], data: ...`). Client pays own gas. No relayer.

### 6.6 Startup (`Makefile`)

- `RPC_URL` must match running Anvil (default `8545`; document if using `8546`).
- `python -m` module path must match package layout in `pyproject.toml`.

---

## 7. E2E Test Plan

1. Anvil mainnet fork; deploy gateway; register agent; fund client.
2. Facilitator + agent server; client pays `GET /weather`.
3. Assert 402 extension `reqHash` matches canonical empty body hash.
4. After settle: `commitments` + `settlementRequestHash[tx] == reqHash`.
5. After 200: headers match local hashes; `settlementResponseHash[tx] == respHash`.
6. `submitFeedback` verified; `hasBeenUsed(feedbackHash)`; `getLastIndex(agentId, client) > 0`.
7. Duplicate same `feedbackHash` → revert.
8. Same `settlementTxHash`, different `feedbackHash` → `usedSettlements` revert.
9. `recordCommitment` twice same tx, different `reqHash` → revert.
10. `recordResponseHash` twice same tx, different `respHash` → revert.
11. Submit verified with wrong `responseHash` → revert.
12. Direct-settlement mode: agent records commitment + response; client verifies without facilitator.

---

## 8. HTML Page Update

File: `x402-erc8004-flow.html`

- Replace `X-Reputation-Proof` with `reqHash` in 402 + mirroring headers on 200 (§2.2).
- Show `recordCommitment` at settle and `recordResponseHash` at 200.
- On-chain: `paymentCommitment`, `settlementRequestHash`, `settlementResponseHash`, `usedSettlements`.

---

## 9. Key Differences from Previous Spec

| Dimension | Old (signature) | New (commitment) |
|-----------|-----------------|------------------|
| Trust anchor | Agent ECDSA on raw req/resp bytes | On-chain payment commitment + response hash bindings |
| HTTP | `X-Reputation-Proof` only | Headers mirror chain (`§2.2`); chain authoritative |
| Settlement binding | None | `settlementRequestHash` + conservative revert on conflict |
| Response binding | Signature over raw bytes | `settlementResponseHash` + canonical JSON hashing |
| Feedback hash | `hash(agentId, req, resp, sig)` | `hash(agentId, reqHash, respHash, value, valueDecimals)` |
| Facilitator feedback API | Optional `POST /feedback` | Removed; EIP-7702 only |
| Encoding | Raw bodies | Canonical JSON (§2.1) |

---

## 10. Open Questions

1. **402 `reqHash` injection:** Confirm `enrich_declaration` (or equivalent) in x402 Python SDK for per-request `erc-8004.reqHash`.
2. **Permissioned `recordCommitment` / `recordResponseHash`:** v2 — agent + facilitator allowlist.
3. **Multi-chain:** Out of scope; per-chain deployments.
4. **Unverified path abuse:** Omitting `reqHash` skips settlement binding — acceptable for v1 demo?
5. **Agent knows `settlementTxHash` on 200:** Requires payment middleware / PAYMENT-RESPONSE to expose settle tx to agent process.

---

## 11. Implementation Notes (spec review)

Findings from review against `naive_implementation/main.py` and contracts — address during implementation:

| Item | Action |
|------|--------|
| Single-file demo | Split per §6.1; fix client order (settle → chain poll → feedback). |
| `reqHash` on 402 | Not possible with current outer-only `ReputationMiddleware` (402 passthrough). Use extension hook or 402-aware middleware. |
| Facilitator `onAfterSettle` | TypeScript repo has it; Python: wrap `POST /settle` handler. |
| `payTo → agentId` | Bootstrap must populate store; facilitator cannot guess `agentId` from address alone. |
| `GET /reputation` | Not in naive facilitator; drop from naive scope or add explicitly. |
| `docs/x402-erc8004-implementation.md` | Update after implementation to point here. |
| `test/conftest.py`, `test_e2e.py` | Specified but not yet present — add with Makefile. |
| Package imports | Align `pyproject.toml` name with `python -m` paths. |
| EIP-7702 attribution | Unchanged — see `naive_implementation/docs/feedback-attribution.md`. |
| Comparison table “client address needed” | Old spec already allowed direct on-chain feedback; real win is settlement + response binding, not facilitator client mapping. |

**Canonical hashing helper (required):** one module used by agent middleware, client, and tests so Foundry + Python + headers stay aligned.
