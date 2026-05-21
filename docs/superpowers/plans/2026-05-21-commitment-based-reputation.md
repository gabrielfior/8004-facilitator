# Commitment-Based Reputation Proof — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace agent-signed proof-of-interaction with on-chain commitment `hash(agentId, reqHash, txHash)` recorded during settlement, enabling censorship-resistant client feedback.

**Architecture:** The agent includes `reqHash = keccak256(reqBody)` in the 402 response's `erc-8004` extension. After settlement, the facilitator (or agent, for direct settlement) computes `commitment = keccak256(agentId, reqHash, settlementTxHash)` and records it on FeedbackGateway. The client verifies the commitment and submits feedback directly via EIP-7702. A `usedSettlements[txHash]` mapping enforces one settlement = one verified feedback.

**Tech Stack:** Solidity 0.8.20 (Forge), Python 3.12 (x402 SDK, web3.py, FastAPI), Anvil (local fork), Foundry for tests.

---

## File Structure

```
naive_implementation/
  Makefile                          # NEW — start/stop all services
  pyproject.toml                    # EXISTING (no changes)
  contracts/
    FeedbackGateway.sol             # MODIFY — add commitments, settlement dedup
    interfaces/
      IReputationRegistry.sol       # EXISTING (no changes)
  src/
    __init__.py                     # NEW (empty)
    setup.py                        # NEW — deploy contracts, register agent, fund
    facilitator/
      __init__.py                   # NEW
      __main__.py                   # NEW — uvicorn entrypoint
      app.py                        # NEW — FastAPI :4022, verify/settle/recordCommitment
    agent_server/
      __init__.py                   # NEW
      __main__.py                   # NEW — uvicorn entrypoint
      app.py                        # NEW — FastAPI :4021, /weather, ReputationMiddleware
    client/
      __init__.py                   # NEW
      __main__.py                   # NEW — asyncio entrypoint
      app.py                        # NEW — pay, verify commitment, submit feedback
    shared/
      __init__.py                   # NEW
      constants.py                  # NEW — addresses, RPC, ports
  test/
    FeedbackGateway.t.sol           # MODIFY — add commitment tests
    conftest.py                     # NEW — Anvil fixture, contract deployment
    test_e2e.py                     # NEW — multi-process e2e
  main.py                           # REMOVE (replaced by separate modules)
  x402-erc8004-flow.html            # MODIFY — update for Model 4
```

---

## Phase 1: HTML Flow Page Update

**Files:**
- Modify: `x402-erc8004-flow.html`

Updates the visual flow diagram to reflect the commitment-based proof model.

**Changes:**
- Replace "ReputationMiddleware signs proof" with "ReputationMiddleware computes reqHash"
- Replace "`X-Reputation-Proof`" with "`reqHash` in 402 extensions + `X-Reputation-Commitment` header"
- Replace "`POST /sign-proof`" with "`FeedbackGateway.recordCommitment(agentId, commitment)`"
- Replace "`markUsed`" with "`submitFeedback` with commitment + settlement dedup"
- Update Additions list to match spec section 8
- Update ASCII flow to show new steps
- Update party descriptions

---

## Phase 2: Update FeedbackGateway.sol

**Files:**
- Modify: `naive_implementation/contracts/FeedbackGateway.sol`

### 2.1 Add commitment mapping + event + settlement dedup mapping

Add to contract state:
```solidity
mapping(uint256 => mapping(bytes32 => bool)) public commitments;
mapping(bytes32 => bool) public usedSettlements;
event CommitmentRecorded(uint256 indexed agentId, bytes32 indexed commitment);
```

### 2.2 Add `recordCommitment` method

```solidity
function recordCommitment(uint256 agentId, bytes32 commitment) external {
    commitments[agentId][commitment] = true;
    emit CommitmentRecorded(agentId, commitment);
}
```

### 2.3 Update `FeedbackParams` struct

Add 3 fields:
```solidity
bytes32 requestHash;
bytes32 responseHash;
bytes32 settlementTxHash;
```

### 2.4 Update `submitFeedback`

Add commitment verification + settlement dedup gate before existing dedup/giveFeedback:
```solidity
function submitFeedback(address registry, FeedbackParams calldata params) external {
    bytes32 commitment = keccak256(abi.encode(params.agentId, params.requestHash, params.settlementTxHash));
    if (commitments[params.agentId][commitment]) {
        require(!usedSettlements[params.settlementTxHash], "Settlement already used");
        usedSettlements[params.settlementTxHash] = true;
    }
    IFeedbackGateway(dedupStore).consumeInteractionHash(params.feedbackHash);
    IReputationRegistry(registry).giveFeedback(
        params.agentId, params.value, params.valueDecimals,
        params.tag1, params.tag2, params.endpoint,
        params.feedbackURI, params.feedbackHash
    );
}
```

---

## Phase 3: Update Foundry Tests

**Files:**
- Modify: `naive_implementation/test/FeedbackGateway.t.sol`

### 3.1 Update `FeedbackParams` in existing tests

Add `requestHash`, `responseHash`, `settlementTxHash` fields to all existing test params structs. Use dummy values like `keccak256("req")`, `keccak256("resp")`, `keccak256("tx")`.

### 3.2 Add commitment test: `test_recordCommitment_stores_and_emits`

Record `commitment = keccak256(abi.encode(1, keccak256("req"), keccak256("tx")))`. Assert `commitments[1][commitment] == true` and `CommitmentRecorded(1, commitment)` emitted.

### 3.3 Add verified path test: `test_submitFeedback_verified_passes`

Record commitment. Submit feedback with matching `requestHash` + `settlementTxHash`. Assert `usedSettlements[txHash] == true`, `hasBeenUsed(feedbackHash) == true`.

### 3.4 Add settlement dedup test: `test_submitFeedback_reverts_when_settlement_reused`

Submit verified feedback once (succeeds). Submit again with same `settlementTxHash` but different `feedbackHash` → reverts with "Settlement already used".

### 3.5 Add unverified path test: `test_submitFeedback_unverified_passes`

Submit feedback with no commitment recorded. Assert it succeeds (dedup only, no settlement check).

### 3.6 Add commitment mismatch test: `test_submitFeedback_reverts_when_commitment_mismatch`

Record commitment for (agentId=1, reqHash=A, txHash=X). Submit with (agentId=1, reqHash=B, txHash=X). Hash doesn't match stored → skipped (unverified path). The wrong reqHash means no matching commitment, so it should proceed as unverified (not revert).

Wait — actually, if there IS a commitment for (1, A, X), and the client submits (1, B, X), the computed commitment `hash(1, B, X)` doesn't match the stored `hash(1, A, X)`. So `commitments[1][hash(1, B, X)]` is false. It falls through to the unverified path. The test should assert it does NOT revert (unverified fallback).

### 3.7 Update `test_submitFeedback_delegated_EOA_uses_global_dedup`

Add `requestHash`, `responseHash`, `settlementTxHash` to the params. Verify `hasBeenUsed(feedbackHash)` and `lastClient` still work correctly.

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

Uses `eth_account`, `web3`, and existing `_deploy_feedback_gateway` / `_register_agent` / `_fund_client` logic from current `main.py`.

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
- `POST /settle` — delegates to `x402Facilitator.settle()`, then calls `_record_commitment()`
  - `_record_commitment(settlementTxHash)`:
    1. Extract `reqHash` from settlement context / payment payload extensions
    2. Compute `commitment = keccak256(agentId, reqHash, settlementTxHash)`
    3. Call `FeedbackGateway.recordCommitment(agentId, commitment)` via web3.py
- `GET /health` — returns ok
- `GET /reputation` — queries ReputationRegistry.getSummary()
- `GET /gateway` — returns FeedbackGateway address

No `POST /feedback` endpoint (clients submit on-chain directly).

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
- PaymentMiddlewareASGI (unchanged from current main.py, from x402 SDK)
- ReputationMiddleware (replaces current proof-signing middleware):
  - On incoming request: `reqHash = Web3.keccak(reqBody)`
  - Attach `reqHash` to `erc-8004` extension in 402 response
  - On outgoing 200: `respHash = Web3.keccak(respBody)`, `fullCommitment = Web3.keccak(agentId, reqHash, respHash)`, attach as `X-Reputation-Commitment` header
  - If settling directly: call `FeedbackGateway.recordCommitment()` after settlement
- `GET /weather` — returns weather report (unchanged)
- `POST /signFeedbackAuth` — if kept, unchanged

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
  2. Extract `reqHash` from 402 erc-8004 extension
  3. Pay via facilitator (x402 HTTP client calls POST /settle)
  4. Receive settlementTxHash from settle response
  5. Re-send request → receive 200 + response body
  6. Compute commitment = `keccak256(agentId, reqHash, settlementTxHash)`
  7. Poll `FeedbackGateway.commitments(agentId, commitment)` until true (or timeout)
  8. Compute `feedbackHash = keccak256(agentId, reqHash, keccak256(respBody), rating)`
   9. Sign EIP-7702 authorization delegating to FeedbackGateway
   10. Submit feedback: send tx `from: client, to: client, authorizationList: [auth], data: submitFeedback(registry, params)`. Client pays own gas. No relayer.
  11. Assert `hasBeenUsed(feedbackHash)` → true

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
6. Client pays → verifies commitment → submits feedback
7. Assert `hasBeenUsed(feedbackHash)` == true
8. Assert `usedSettlements[txHash]` == true (via contract read)
9. Re-submit same settlement → must not crash (unverified path, or revert if verified)
10. Kill subprocesses

---

## Execution Order

Each phase produces a working, testable artifact:

1. **Phase 1** — Visual documentation updated
2. **Phase 2–3** — Contract compiles, tests pass
3. **Phase 4–8** — Python modules run independently
4. **Phase 9** — Full integration verified

Commit after each phase. Ask for feedback before proceeding to next phase.
