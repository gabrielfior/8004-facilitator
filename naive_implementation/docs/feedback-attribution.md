# Feedback attribution (client as author)

ERC-8004 `ReputationRegistry.giveFeedback()` records **`msg.sender` as `clientAddress`**. The feedback giver must be the paying client EOA, not a relay contract.

## Chosen approach: EIP-7702 + `FeedbackGateway` (one contract)

[`FeedbackGateway.sol`](../contracts/FeedbackGateway.sol) is deployed once at `0xGW` and serves two roles:

1. **Singleton dedup store** — `usedHashes` at `dedupStore` (`immutable = address(this)` on deploy).
2. **EIP-7702 delegate bytecode** — client EOA delegates to `0xGW`, then calls `submitFeedback(registry, params)`.

### Why not Multicall3?

[Multicall3 `aggregate3`](https://github.com/mds1/multicall/blob/master/src/Multicall3.sol) uses `target.call(callData)`. Subcalls see **`msg.sender == Multicall3`**, not the client. That breaks ERC-8004 attribution.

### Why `dedupStore` external call?

When a client EOA runs delegated gateway bytecode, **storage is the EOA’s**, not the deployment’s. Global dedup must live on the singleton:

```solidity
IFeedbackGateway(dedupStore).consumeInteractionHash(params.feedbackHash); // global mapping at 0xGW
IReputationRegistry(registry).giveFeedback(...); // msg.sender = client EOA
```

If step 2 reverts, the whole transaction reverts (including consume).

### Transaction shape (naive demo)

| Field | Value |
|-------|--------|
| `from` | Facilitator (pays gas) or client |
| `to` | Client EOA |
| `authorizationList` | Client-signed delegation to `feedback_gateway` |
| `data` | `submitFeedback(REPUTATION_REGISTRY, FeedbackParams)` |

See [`main.py`](../main.py) `_sign_eip7702_authorization` and `_submit_feedback`. Gas: `eth_estimateGas` + 20% buffer for the tx limit; logs `gasUsed` vs limit after mining.

## Events: no gateway logs (use `NewFeedback` only)

| Event | Emitter | Purpose |
|-------|---------|---------|
| **`NewFeedback`** | `ReputationRegistry` | Canonical ERC-8004 reputation signal: `agentId`, **`clientAddress`** (author), `value`, tags, `feedbackHash`, `feedbackURI`, etc. Indexers and `getSummary` use this. |
| ~~`FeedbackSubmitted`~~ | ~~Gateway~~ | Removed — duplicated `agentId` + client + `feedbackHash` already in `NewFeedback`; no extra trust signal. |
| ~~`InteractionHashConsumed`~~ | ~~Gateway~~ | Removed — dedup state is `usedHashes` / `hasBeenUsed()`; consumer is not needed on-chain if you already read `NewFeedback`. |

`InteractionHashConsumed` was only useful for debugging *who* called `consumeInteractionHash` in isolation. In the normal path, consume and `giveFeedback` happen in one tx and attribution comes from the registry.

## Alternatives (not used)

| Approach | Client as author? | Notes |
|----------|-------------------|--------|
| **EIP-7702 + FeedbackGateway** (current) | Yes | Matches [`AgentRegistrationDelegate`](../../contracts/AgentRegistrationDelegate.sol) pattern |
| Multicall3 batch | No | Wrong `msg.sender` on registry |
| Gateway relays `giveFeedback` | No | Gateway becomes author |
| Two separate txs | Yes | Race between consume and feedback |
| ERC-2771 forwarder | Yes | Requires registry support |
| `giveFeedbackFor(client, …)` on registry | Yes | Needs spec change |

## Forge dependency

Official ABIs: `lib/erc-8004-contracts` (`forge install erc-8004/erc-8004-contracts`). Remapping: `@erc8004/` in [`foundry.toml`](../foundry.toml).

## Verification

- Unit: `test_submitFeedback_delegated_EOA_uses_global_dedup` (Foundry `vm.etch` simulates 7702).
- Live: `getLastIndex(agentId, clientAddress)` increases after demo run; `hasBeenUsed(feedbackHash)` is true on deployed gateway.
