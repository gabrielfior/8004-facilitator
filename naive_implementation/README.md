# Naive x402 local demo

Single-process demo of the [x402 Python SDK](https://github.com/coinbase/x402/tree/main/python/x402): **facilitator**, **resource server**, and **paying client** against a local **Anvil** node.

## Prerequisites

- [Foundry](https://book.getfoundry.sh/) (`anvil`, `forge`)
- [uv](https://docs.astral.sh/uv/)

## Run

**Terminal 1 — start Anvil:**

```bash
anvil
```

**Terminal 2 — run the demo:**

```bash
cd naive_implementation
forge build   # once: compiles contracts/MockUSDC.sol (EIP-3009)
uv sync
uv run python main.py
```

The script will:

1. Deploy `MockUSDC` (EIP-3009) on Anvil
2. Mint USDC to the client account
3. Start the facilitator on `http://127.0.0.1:4022`
4. Start the resource server on `http://127.0.0.1:4021`
5. Pay for `GET /weather` and print the settlement tx hash

## Optional environment

| Variable | Default |
|----------|---------|
| `RPC_URL` | `http://127.0.0.1:8545` |
| `FACILITATOR_PRIVATE_KEY` | Anvil account #0 |
| `CLIENT_PRIVATE_KEY` | Anvil account #1 |
| `AGENT_PRIVATE_KEY` | Anvil account #2 |
| `FACILITATOR_PORT` | `4022` |
| `SERVER_PORT` | `4021` |
