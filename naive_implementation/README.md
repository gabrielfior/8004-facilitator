# Naive x402 local demo

Single-process demo of the [x402 Python SDK](https://github.com/coinbase/x402/tree/main/python/x402): **facilitator**, **resource server**, and **paying client** against a local **Anvil** node forking Ethereum mainnet.

## Prerequisites

- [Foundry](https://book.getfoundry.sh/) (`anvil` >= 1.6.0-nightly with Prague hardfork, `forge`)
- [uv](https://docs.astral.sh/uv/)
- A mainnet RPC URL (e.g. Alchemy, Infura, or a public endpoint like `https://rpc.flashbots.net`)

## Quickstart (E2E test)

One command — starts Anvil fork, deploys contracts, runs the full payment + EIP-7702 feedback flow, and verifies on-chain state:

```bash
cd naive_implementation
forge build
uv sync
FORK_RPC_URL=<MAINNET_RPC_URL> uv run pytest test/test_e2e.py -v --timeout=180
```

The test does everything automatically:
1. Starts Anvil with `--hardfork prague --fork-url $FORK_RPC_URL --chain-id 1`
2. Deploys `FeedbackGateway`
3. Registers an agent on the mainnet `IdentityRegistry` (`0x8004A169...`)
4. Funds the client with ETH, USDC, and DAI
5. Starts facilitator (`:4022`) and agent server (`:4021`)
6. Runs the paying client (x402 USDC payment for `GET /weather`)
7. Submits feedback via EIP-7702 delegation to the mainnet `ReputationRegistry` (`0x8004BAa1...`)
8. Asserts feedback is attributed to the client EOA on-chain

## Manual step-by-step

**Terminal 1 — start Anvil:**

```bash
anvil --hardfork prague --fork-url <MAINNET_RPC_URL> --chain-id 1
```

**Terminal 2 — bootstrap + run services + client:**

```bash
cd naive_implementation

# Build contracts
forge build

# Bootstrap: deploy FeedbackGateway, register agent, fund client (writes /tmp/setup.env)
RPC_URL=http://127.0.0.1:8545 uv run python -m src.setup

# Start facilitator + agent server
RPC_URL=http://127.0.0.1:8545 make start-services

# Run the paying client (EIP-7702 feedback submission)
EIP_7702_SUPPORTED=true RPC_URL=http://127.0.0.1:8545 make run-client
```

## Feedback attribution (EIP-7702)

The client submits feedback using an EIP-7702 type 4 transaction: the client EOA delegates to the `FeedbackGateway` contract, which calls `giveFeedback` on the **mainnet** `ReputationRegistry` at `0x8004BAa17C55a88189AE136b182e5fdA19dE9b63`. Because the delegated code runs at the EOA's address, `msg.sender` in the registry is the client EOA — so feedback is correctly attributed to the client.

No mock registry is needed. The mainnet `ReputationRegistry.giveFeedback` is permissionless (anyone can leave feedback on any agent, as long as they are not the agent's owner/operator).

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FORK_RPC_URL` | (required) | Mainnet RPC URL for the Anvil fork |
| `RPC_URL` | `http://127.0.0.1:8545` | RPC endpoint to connect to (local Anvil) |
| `EIP_7702_SUPPORTED` | `false` | Set to `true` to submit feedback via EIP-7702 delegation |
| `FACILITATOR_PRIVATE_KEY` | Anvil account #0 | Facilitator's signing key |
| `CLIENT_PRIVATE_KEY` | fresh key | Client's key (funded by setup) |
| `AGENT_PRIVATE_KEY` | fresh key | Agent owner's key |
| `PAYMENT_TOKEN` | `usdc` | `usdc` or `dai` |
| `FACILITATOR_PORT` | `4022` | Facilitator HTTP port |
| `SERVER_PORT` | `4021` | Agent server HTTP port |

## Contracts

| Contract | Address (mainnet) | Description |
|----------|-------------------|-------------|
| `ReputationRegistry` | `0x8004BAa17C55a88189AE136b182e5fdA19dE9b63` | ERC-8004 feedback storage (permissionless `giveFeedback`) |
| `IdentityRegistry` | `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` | ERC-8004 agent registration |
| `FeedbackGateway` | deployed by setup | EIP-7702 delegate for settlement verification + dedup + feedback |
