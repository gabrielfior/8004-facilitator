# Naive x402 local demo

Single-process demo of the [x402 Python SDK](https://github.com/coinbase/x402/tree/main/python/x402): **facilitator**, **resource server**, and **paying client** against a local **Anvil** node.

## Prerequisites

- [Foundry](https://book.getfoundry.sh/) (`anvil`, `forge`)
- [uv](https://docs.astral.sh/uv/)
- Anvil with **EIP-7702** support (recent Foundry)

## Dependencies (Foundry)

```bash
cd naive_implementation
forge install erc-8004/erc-8004-contracts
forge install foundry-rs/forge-std
forge build
forge test
```

## Feedback (EIP-7702)

[`FeedbackGateway`](contracts/FeedbackGateway.sol) is deployed once. The **client** signs EIP-7702 authorization delegating to that address; the facilitator sends `submitFeedback` **to the client EOA** so `ReputationRegistry` records the client as author. See [docs/feedback-attribution.md](docs/feedback-attribution.md).

## Run

**Terminal 1 — start Anvil:**

```bash
anvil --fork-url <RPC_URL> --chain-id 1
```

**Terminal 2 — run the demo:**

```bash
cd naive_implementation
forge build
uv sync
uv run python main.py
```

The script will:

1. Transfer mainnet USDC to the client account (from facilitator on the fork)
2. Deploy `FeedbackGateway` and register an agent on ERC-8004
3. Start the facilitator on `http://127.0.0.1:4022`
4. Start the resource server on `http://127.0.0.1:4021`
5. Pay for `GET /weather`, then submit feedback via EIP-7702 `submitFeedback`

## Optional environment

| Variable | Default |
|----------|---------|
| `RPC_URL` | `http://127.0.0.1:8545` |
| `FACILITATOR_PRIVATE_KEY` | Anvil account #0 |
| `CLIENT_PRIVATE_KEY` | fresh key (funded by facilitator) |
| `PAYMENT_TOKEN` | `usdc` (mainnet USDC) or `dai` |
| `AGENT_PRIVATE_KEY` | fresh key generated if unset |
| `FACILITATOR_PORT` | `4022` |
| `SERVER_PORT` | `4021` |
