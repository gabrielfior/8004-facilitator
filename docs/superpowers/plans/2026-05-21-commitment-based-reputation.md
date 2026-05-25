# Settlement-Payer Reputation Proof — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace agent-signed proof-of-interaction with on-chain settlement-payer binding `settlementPayer[txHash] = payer`. The client (who paid) submits verified feedback directly via EIP-7702. No reqHash, no commitment preimage, no canonical JSON hashing needed.

**Architecture:** After settlement, the facilitator (or agent) records `settlementPayer[txHash] = clientAddress` on FeedbackGateway. The client polls the mapping, then submits feedback via EIP-7702 self-call. The contract checks `settlementPayer[txHash] == msg.sender` for the verified path (with `usedSettlements[txHash]` dedup). If no settlement binding exists, feedback is unverified (dedup only).

**Tech Stack:** Solidity 0.8.20 (Forge), Python 3.12 (x402 SDK, web3.py, FastAPI), Anvil (local fork), Foundry for tests.

---

## File Structure

```
naive_implementation/
  Makefile                          # NEW — start/stop all services
  pyproject.toml                    # EXISTING (no changes)
  contracts/
    FeedbackGateway.sol             # MODIFY — add settlementPayer, usedSettlements, recordSettlement
    interfaces/
      IReputationRegistry.sol       # EXISTING (no changes)
  src/
    __init__.py                     # NEW (empty)
    setup.py                        # NEW — deploy contracts, register agent, fund
    facilitator/
      __init__.py                   # NEW
      __main__.py                   # NEW — uvicorn entrypoint
      app.py                        # NEW — FastAPI :4022, verify/settle/recordSettlement
    agent_server/
      __init__.py                   # NEW
      __main__.py                   # NEW — uvicorn entrypoint
      app.py                        # NEW — FastAPI :4021, /weather, no reputation logic
    client/
      __init__.py                   # NEW
      __main__.py                   # NEW — asyncio entrypoint
      app.py                        # NEW — pay, poll settlementPayer, submit feedback
    shared/
      __init__.py                   # NEW
      constants.py                  # NEW — addresses, RPC, ports
  test/
    FeedbackGateway.t.sol           # MODIFY — add settlementPayer tests
    conftest.py                     # NEW — Anvil fixture, contract deployment
    test_e2e.py                     # NEW — multi-process e2e
  main.py                           # REMOVE (replaced by separate modules)
  x402-erc8004-flow.html            # MODIFY — update for settlement-payer model
```

---

## Phase 1: HTML Flow Page Update

**Files:**
- Modify: `x402-erc8004-flow.html`

Updates the visual flow diagram to reflect the settlement-payer proof model.

**Changes:**
- **Remove:** ReputationMiddleware, reqHash in 402, `X-Reputation-Commitment` header, canonical hashing references
- **Replace** "`recordCommitment(agentId, commitment)`" with "`recordSettlement(txHash, payer)`"
- **Replace** "commitment hash verification" with "`settlementPayer[txHash] == msg.sender` check"
- **Simplify** detailed feedback section (8a-8d) — no reqHash/respHash/hash reconstruction
- Update Additions list to match spec
- Update ASCII flow
- Update Architecture components

---

## Phase 2: Update FeedbackGateway.sol

**Files:**
- Modify: `naive_implementation/contracts/FeedbackGateway.sol`

### 2.1 Add settlement mappings + event

```solidity
mapping(bytes32 => address) public settlementPayer;  // txHash => payer
mapping(bytes32 => bool) public usedSettlements;      // txHash => consumed (verified path)

event SettlementRecorded(bytes32 indexed txHash, address indexed payer);
```

### 2.2 Add `recordSettlement` method

```solidity
function recordSettlement(bytes32 txHash, address payer) external {
    if (settlementPayer[txHash] == address(0)) {
        settlementPayer[txHash] = payer;
        emit SettlementRecorded(txHash, payer);
    }
}
```

First-writer semantics: once set, subsequent calls are no-ops. This prevents a front-runner from overwriting the real payer. Permissionless for v1 — wrong payer only blocks the real client's verified path (they can still submit unverified).

### 2.3 Update `submitFeedback`

Change signature to accept optional `bytes32 settlementTxHash`:

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
        params.agentId, params.value, params.valueDecimals,
        params.tag1, params.tag2, params.endpoint,
        params.feedbackURI, params.feedbackHash
    );
}
```

No changes to `FeedbackParams` struct.

---

## Phase 3: Update Foundry Tests

**Files:**
- Modify: `naive_implementation/test/FeedbackGateway.t.sol`

### 3.1 Add settlement test: `test_recordSettlement_stores_and_emits`

Record `settlementPayer[txHash] = alice`. Assert mapping returns `alice`. Assert `SettlementRecorded(txHash, alice)` emitted.

### 3.2 Add first-writer test: `test_recordSettlement_first_writer_wins`

Record `settlementPayer[txHash] = alice`. Record again with `bob`. Assert mapping still returns `alice`.

### 3.3 Add verified path test: `test_submitFeedback_verified_passes`

Record settlement for client. Submit feedback via delegation with matching txHash. Assert `usedSettlements[txHash] == true`, `hasBeenUsed(feedbackHash) == true`.

### 3.4 Add settlement dedup test: `test_submitFeedback_reverts_when_settlement_reused`

Submit verified feedback once (succeeds). Submit again with same txHash → reverts "Settlement already used".

### 3.5 Add wrong payer test: `test_submitFeedback_unverified_when_wrong_payer`

Record settlement for alice. Submit feedback as bob with same txHash. Assert it succeeds as unverified (dedup only, no settlement binding).

### 3.6 Add no txHash test: `test_submitFeedback_unverified_when_no_txHash`

Submit feedback with `settlementTxHash = bytes32(0)`. Assert it succeeds (dedup only, no settlement check).

### 3.7 Update delegation + dedup test

Update `test_submitFeedback_delegated_EOA_uses_global_dedup` — add `settlementTxHash` parameter (can be `bytes32(0)` to keep test focused on dedup behavior).

---

## Phase 4: Python Process Separation

**Files:**
- Create: `naive_implementation/src/__init__.py` (empty)
- Create: `naive_implementation/src/shared/__init__.py` (empty)
- Create: `naive_implementation/src/shared/constants.py`
- Create: `naive_implementation/src/setup.py`

### 4.1 `src/shared/constants.py`

Shared configuration:

```python
ROOT = Path(__file__).resolve().parent.parent.parent
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
NETWORK: Network = "eip155:1"
FACILITATOR_PORT = int(os.getenv("FACILITATOR_PORT", "4022"))
SERVER_PORT = int(os.getenv("SERVER_PORT", "4021"))
FACILITATOR_URL = f"http://127.0.0.1:{FACILITATOR_PORT}"
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

MAINNET_USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI_ADDRESS = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPUTATION_REGISTRY = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"
FACILITATOR_KEY = os.getenv("FACILITATOR_KEY", FACILITATOR_DEFAULT_KEY)
CLIENT_KEY = os.getenv("CLIENT_KEY", CLIENT_DEFAULT_KEY)
```

### 4.2 `src/setup.py`

One-time bootstrap: deploys FeedbackGateway, registers agent on IdentityRegistry, funds client with USDC/DAI. Prints all addresses for subsequent processes.

Uses `eth_account`, `web3`, and existing deployment logic from current `main.py`.

### 4.3 Module entrypoints

Each module gets a `__main__.py`:
- `src/facilitator/__main__.py`: `uvicorn.run(facilitator_app, port=4022)`
- `src/agent_server/__main__.py`: `uvicorn.run(agent_server_app, port=4021)`
- `src/client/__main__.py`: `asyncio.run(run_paying_client())`

---

## Phase 5: Python Facilitator Module

**Files:**
- Create: `naive_implementation/src/facilitator/__init__.py` (empty)
- Create: `naive_implementation/src/facilitator/__main__.py`
- Create: `naive_implementation/src/facilitator/app.py`

### 5.1 `src/facilitator/app.py`

FastAPI app running on :4022:
- `POST /verify` — delegates to `x402Facilitator.verify()`
- `POST /settle` — delegates to `x402Facilitator.settle()`, then:
  - Extract `settlementTxHash` from settle response
  - Extract `clientAddress` (payer) from settlement context
  - Call `FeedbackGateway.recordSettlement(txHash, clientAddress)` via web3.py
- `GET /health` — returns ok
- `GET /gateway` — returns FeedbackGateway address

No reputation endpoints. No reqHash extraction.

### 5.2 `src/facilitator/__main__.py`

```python
import uvicorn
from .app import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=4022)
```

---

## Phase 6: Python Agent Server Module

**Files:**
- Create: `naive_implementation/src/agent_server/__init__.py` (empty)
- Create: `naive_implementation/src/agent_server/__main__.py`
- Create: `naive_implementation/src/agent_server/app.py`

### 6.1 `src/agent_server/app.py`

FastAPI app running on :4021:
- PaymentMiddlewareASGI (unchanged, from x402 SDK)
- `GET /weather` — returns weather report (unchanged)
- **No ReputationMiddleware** — no reqHash, no reputation headers, no proof logic

If direct-settlement mode: after settlement, agent calls `FeedbackGateway.recordSettlement()` itself.

### 6.2 `src/agent_server/__main__.py`

```python
import uvicorn
from .app import create_app

if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=4021)
```

---

## Phase 7: Python Client Module

**Files:**
- Create: `naive_implementation/src/client/__init__.py` (empty)
- Create: `naive_implementation/src/client/__main__.py`
- Create: `naive_implementation/src/client/app.py`

### 7.1 `src/client/app.py`

Client logic:
- Initialize `x402Client` + `EthAccountSignerWithRPC` + `x402_httpx_transport`
- `async def run_paying_client()`:
  1. `GET /weather` via `x402_httpx_transport`
  2. Handle 402, sign payment, pay via facilitator
  3. Re-send request → receive 200 + response body
  4. Extract `settlementTxHash` from `PAYMENT-RESPONSE` header
  5. Poll `FeedbackGateway.settlementPayer[txHash]` until `!= address(0)` (or timeout)
  6. Compute `feedbackHash = Web3.solidity_keccak(["uint256", "bytes32"], [agentId, settlementTxHash])`
  7. Sign EIP-7702 authorization delegating to FeedbackGateway
  8. Submit feedback: send tx `from: client, to: client, authorizationList: [auth], data: submitFeedback(registry, params, txHash)`
  9. Assert `hasBeenUsed(feedbackHash) == true`

### 7.2 `src/client/__main__.py`

```python
import asyncio
from .app import run_paying_client

if __name__ == "__main__":
    asyncio.run(run_paying_client())
```

---

## Phase 8: Makefile

**Files:**
- Create: `naive_implementation/Makefile`

```makefile
.PHONY: setup start stop run-client

RPC_URL ?= http://127.0.0.1:8545

setup:
	python -m naive_implementation.src.setup

start: start-anvil start-facilitator start-agent-server
	@echo "All services started."

stop:
	@pkill -f "anvil" 2>/dev/null || true
	@pkill -f "src/facilitator" 2>/dev/null || true
	@pkill -f "src/agent_server" 2>/dev/null || true
	@sleep 1
	@echo "All services stopped."

start-anvil:
	@echo "Starting Anvil fork..."
	anvil --fork-url $(RPC_URL) --chain-id 1 &>/tmp/anvil.log &
	@sleep 2
	@echo "Anvil ready (PID: $$(pgrep -f 'anvil --fork' 2>/dev/null))"

start-facilitator:
	python -m naive_implementation.src.facilitator &>/tmp/facilitator.log &
	@echo "Facilitator started (PID: $$!)"

start-agent-server:
	python -m naive_implementation.src.agent_server &>/tmp/agent_server.log &
	@echo "Agent server started (PID: $$!)"

run-client:
	python -m naive_implementation.src.client
```

---

## Phase 9: E2E Test

**Files:**
- Create: `naive_implementation/test/conftest.py`
- Create: `naive_implementation/test/test_e2e.py`

### 9.1 `conftest.py`

Pytest fixture: starts Anvil, runs setup, yields contract addresses + accounts. Stops Anvil on teardown.

### 9.2 `test_e2e.py`

Full integration test:
1. Start Anvil fork (fixture)
2. Deploy FeedbackGateway (using setup)
3. Register agent + fund client
4. Start facilitator in subprocess
5. Start agent server in subprocess
6. Client pays → polls settlementPayer → submits feedback
7. Assert `hasBeenUsed(feedbackHash) == true`
8. Assert `usedSettlements[txHash] == true` (via contract read)
9. Re-submit same settlement → unverified path succeeds (dedup only since settlement already used)
10. Re-submit same feedbackHash → reverts
11. Kill subprocesses

---

## Execution Order

Each phase produces a working, testable artifact:

1. **Phase 1** — Visual documentation updated
2. **Phase 2–3** — Contract compiles, tests pass
3. **Phase 4–8** — Python modules run independently
4. **Phase 9** — Full integration verified

Commit after each phase. Ask for feedback before proceeding to next phase.
