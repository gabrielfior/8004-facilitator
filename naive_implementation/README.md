# Naive x402 local demo

Single-process demo of the [x402 Python SDK](https://github.com/coinbase/x402/tree/main/python/x402): **facilitator**, **resource server**, and **paying client** against a local **Anvil** node.

## Prerequisites

- [Foundry](https://book.getfoundry.sh/) (`anvil`, `forge`)
- [uv](https://docs.astral.sh/uv/)

## Run

**Terminal 1 — start Anvil:**

```bash
anvil --fork-url <RPC_URL> --chain-id 1
```

**Terminal 2 — run the demo:**

```bash
cd naive_implementation
forge build   # once: compiles contracts/FeedbackGateway.sol
uv sync
uv run python main.py
```

The script will:

1. Transfer mainnet USDC to the client account (from facilitator on the fork)
2. Start the facilitator on `http://127.0.0.1:4022`
3. Start the resource server on `http://127.0.0.1:4021`
4. Pay for `GET /weather` with USDC (EIP-3009) and print the settlement tx hash

## Optional environment

| Variable | Default |
|----------|---------|
| `RPC_URL` | `http://127.0.0.1:8545` |
| `FACILITATOR_PRIVATE_KEY` | Anvil account #0 |
| `CLIENT_PRIVATE_KEY` | fresh key (funded by facilitator) |
| `PAYMENT_TOKEN` | `usdc` (mainnet USDC) or `dai` |
| `AGENT_PRIVATE_KEY` | Anvil account #2 |
| `FACILITATOR_PORT` | `4022` |
| `SERVER_PORT` | `4021` |
