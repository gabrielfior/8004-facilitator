# x402 × ERC-8004 Reputation — On-Chain Dedup via FeedbackGateway

**Date:** 2026-05-18
**Status:** Spec draft

## Problem

x402 and ERC-8004 are currently separate standards. Clients pay agents via x402 but have no way to leave on-chain reputation feedback tied to a specific paid interaction. Any dedup mechanism that relies on the facilitator (Redis, subgraph) gives the facilitator veto power over feedback.

## Solution

Four actors, one new contract:

| Actor | Role |
|---|---|
| **Client** | Wants service, pays via x402, optionally leaves feedback |
| **Agent** | Provides service, receives payment, signs responses with EOA key |
| **Facilitator** | Relays payments and optionally relays feedback (NOT trusted for dedup) |
| **FeedbackGateway** (new) | Permissionless on-chain contract — enforces dedup via `usedHashes` mapping |

**Key principle:** The facilitator can censor feedback relay — that's acceptable because the client can submit feedback directly to the `FeedbackGateway` on-chain as a fallback.

## Architecture

### Full End-to-End Flow

```
Step 1: Client → Agent                    HTTP request for service
Step 2: Agent → Client                    HTTP 402 + PaymentRequired (reputation extension in accepts[].extensions)
Step 3: Client → Facilitator              POST /settle with Permit2 signature
Step 4: Facilitator                       Settles on-chain → returns SettleResponse with tx hash
Step 5: Client → Agent                    Original request + PAYMENT-SIGNATURE header
Step 6: Agent → Client                    Service response + signed proof-of-interaction (in PAYMENT-RESPONSE header extensions)
Step 7: Client optionally submits feedback:
        Path A → Facilitator → FeedbackGateway.giveFeedback()     (gasless for client)
        Path B → FeedbackGateway.giveFeedback() directly          (client pays gas — fallback)
```

### Proof-of-Interaction (Step 6)

```
dataHash = keccak256(request_body || response_body)
interactionHash = keccak256("x402:reputation:v1" || taskRef || dataHash)
signature = ECDSA_sign(interactionHash, agent_private_key)
```

- `taskRef` = CAIP-220 reference: `eip155:{chainId}/{transactionHash}`
- Agent signs BEFORE knowing feedback content (signs at response time). Prevents selective signing.

### Dedup (Step 7)

```
FeedbackGateway.usedHashes[interactionHash] → bool
  false → set to true → call ReputationRegistry.giveFeedback(feedbackHash=interactionHash)
  true  → revert "duplicate interaction hash"
```

## Contracts

### FeedbackGateway.sol

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IReputationRegistry {
    function giveFeedback(
        uint256 agentId,
        int128 value,
        uint8 valueDecimals,
        string calldata tag1,
        string calldata tag2,
        string calldata endpoint,
        string calldata feedbackURI,
        bytes32 feedbackHash
    ) external;
}

struct FeedbackParams {
    uint256 agentId;
    int128 value;
    uint8 valueDecimals;
    string tag1;
    string tag2;
    string endpoint;
    string feedbackURI;
    bytes32 feedbackHash;
}

contract FeedbackGateway {
    IReputationRegistry public immutable reputationRegistry;
    mapping(bytes32 => bool) public usedHashes;

    error DuplicateHash(bytes32 hash);
    error FeedbackReverted(bytes reason);

    event FeedbackSubmitted(
        uint256 indexed agentId,
        address indexed caller,
        bytes32 indexed interactionHash
    );

    constructor(address _reputationRegistry) {
        reputationRegistry = IReputationRegistry(_reputationRegistry);
    }

    /// @notice Submit feedback with dedup enforcement.
    /// @dev Marks hash before calling ReputationRegistry; unmarks on failure so client can retry.
    function giveFeedback(FeedbackParams calldata params) external {
        if (usedHashes[params.feedbackHash]) {
            revert DuplicateHash(params.feedbackHash);
        }
        usedHashes[params.feedbackHash] = true;

        (bool success, bytes memory returnData) = address(reputationRegistry).call(
            abi.encodeWithSelector(
                IReputationRegistry.giveFeedback.selector,
                params.agentId, params.value, params.valueDecimals,
                params.tag1, params.tag2, params.endpoint,
                params.feedbackURI, params.feedbackHash
            )
        );
        if (!success) {
            usedHashes[params.feedbackHash] = false;
            revert FeedbackReverted(returnData);
        }

        emit FeedbackSubmitted(params.agentId, msg.sender, params.feedbackHash);
    }

    function hasBeenUsed(bytes32 interactionHash) external view returns (bool) {
        return usedHashes[interactionHash];
    }
}
```

### Key properties

- **Permissionless:** Anyone can call `giveFeedback()`. The facilitator calls it OR the client calls it directly.
- **Dedup at EVM level:** `usedHashes` mapping prevents double-counting even if multiple facilitators or direct submissions target the same interaction.
- **Reentrancy-safe:** Hash is marked before the external call, only unmarked on failure.
- **Retriable:** If the ReputationRegistry call reverts (e.g., invalid value range), the hash is unmarked and the client can fix and retry.
- **Verifiable:** Client checks `hasBeenUsed(interactionHash)` on-chain to confirm their feedback landed.

## Coinbase x402 SDK Integration (Python)

### Dependencies

Uses the published `x402` pip package — no fork, no wrapper classes.

```toml
# pyproject.toml
[project]
dependencies = [
    "x402[httpx,fastapi,evm]>=2.8.0",
    "web3>=6.0.0",
    "pydantic>=2.0.0",
]
```

### Package Structure

```
python/
  pyproject.toml                          # depends on x402[httpx,fastapi,evm]
  src/
    signing.py                            # compute_data_hash, compute_interaction_hash, ECDSA sign/verify
    types.py                              # pydantic model for ProofOfInteraction
    extensions/
      reputation_server.py                # ResourceServerExtension — wires into x402ResourceServer
      reputation_facilitator.py           # FacilitatorExtension — wires into x402Facilitator
    feedback_gateway/
      contract/
        FeedbackGateway.sol
        deploy.py                         # Forge script for Anvil
      abi.json
  tests/
    conftest.py                           # Anvil fixture + contract deployment
    test_signing.py                       # unit tests
    test_e2e.py                           # full Anvil e2e: pay → sign → feedback → dedup
```

### Facilitator Extension (Python)

```python
# src/extensions/reputation_facilitator.py
from x402.interfaces import FacilitatorExtension  # defined in x402's interfaces module

def register_reputation(facilitator):
    """Coinbase pattern: register extension via existing API."""
    facilitator.register_extension(FacilitatorExtension(key="reputation"))
```

No wrapper class. Two lines. The extension is then accessible inside any `SchemeNetworkFacilitator` implementation via `context.get_extension("reputation")` using the `FacilitatorContext` object passed to `verify()`/`settle()`.

### Agent Server Extension (Python)

Follows Coinbase's `ResourceServerExtension` protocol:

```python
# src/extensions/reputation_server.py
from x402 import ResourceServerExtension  # published in the x402 package

class ReputationServerExtension(ResourceServerExtension):
    """Coinbase-pattern extension that signs responses with proof-of-interaction."""

    @property
    def key(self) -> str:
        return "reputation"

    def __init__(self, agent_key: str, agent_address: str):
        self._agent_key = agent_key
        self._agent_address = agent_address

    def enrich_declaration(self, declaration: dict, transport_context: dict) -> dict:
        """Coinbase pattern: enrich PaymentRequired.extensions with agent identity."""
        declaration["agentAddress"] = self._agent_address
        return declaration
```

This class implements the `ResourceServerExtension` Protocol from the Coinbase SDK and is registered via `server.register_extension()` — no custom middleware needed.

### Client-Side Proof Extraction (Python)

```python
# In application code using x402Client directly:
from src.types import ProofOfInteraction

# After successful payment, proof is in PAYMENT-RESPONSE extensions
if response.extensions and "reputation" in response.extensions:
    rep = response.extensions["reputation"]
    proof = ProofOfInteraction(
        taskRef=response.transaction,
        dataHash=rep["dataHash"],
        agentAddress=rep["agentAddress"],
        agentSignature=rep["agentSignature"],
    )
```

No wrapper class. Client uses `x402Client` directly.

## Facilitator Changes (TypeScript, this repo)

The existing TypeScript facilitator replaces Redis dedup with `FeedbackGateway` calls.

### Updated `POST /feedback` flow

```
1. Validate proof-of-interaction signature (same as before)
2. Call FeedbackGateway.giveFeedback() via viem walletClient.writeContract
3. If tx reverts with DuplicateHash → 409 Conflict
4. If tx succeeds → 200 OK
5. If facilitator is offline/censoring → client submits directly on-chain (fallback)
```

### New endpoints

| Endpoint | Purpose |
|---|---|
| `GET /feedback/status?interactionHash=0x...` | Queries `FeedbackGateway.hasBeenUsed()` |
| `GET /feedback/gateway` | Returns the `FeedbackGateway` contract address |
| `POST /feedback` | Submits feedback (relayed to `FeedbackGateway`) — proves proof-of-interaction validation before forwarding |

## E2E Test Plan

1. Spin up Anvil node (local ephemeral chain)
2. Deploy mock ERC-8004 ReputationRegistry
3. Deploy `FeedbackGateway(reputationRegistryAddr)`
4. Start facilitator (wired to Anvil)
5. Start agent server (wired to facilitator, has agent private key)
6. Client sends HTTP request for service → gets 402 `PaymentRequired`
7. Client creates payment payload via `x402Client` → sends to facilitator → `POST /settle`
8. Client re-sends original request with `PAYMENT-SIGNATURE` header
9. Agent verifies payment → returns response + `proof-of-interaction`
10. Client extracts `ProofOfInteraction` from response extensions
11. Client submits feedback with proof to facilitator → facilitator calls `FeedbackGateway.giveFeedback()`
12. Client submits same feedback again → 409
13. Client verifies on-chain: `FeedbackGateway.hasBeenUsed(interactionHash)` → `true`

## Comparison: Old Plan vs New Plan

| Dimension | Old Plan (Redis + Subgraph) | New Plan (FeedbackGateway) |
|---|---|---|
| Dedup location | Facilitator (Redis) + Subgraph | On-chain (FeedbackGateway mapping) |
| Fallback path | None | Client submits directly to contract |
| Facilitator trust | Trusted for dedup | Untrusted for dedup (fallback always available) |
| New contracts | None | FeedbackGateway.sol |
| New infra | Redis, subgraph deployment | None |
| Coinbase x402 SDK | Custom extensions from scratch | Uses published `x402` package extensions directly |

## Open Questions

1. **Multi-chain feedback:** If payment is on Base but feedback goes to Ethereum, the taskRef's chainId mismatches. Solution: deploy `FeedbackGateway` on the same chain as the ReputationRegistry. Client submits on whichever chain the agent's reputation lives. (The hash binds to the payment chain, but the feedback lives on the registry chain.)
2. **Gas costs for direct submission:** Client needs ETH for gas on fallback path. Acceptable — it's the same as any other on-chain interaction. Facilitator relay is the default gasless path.
