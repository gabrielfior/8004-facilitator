# x402 × ERC-8004 Reputation Integration — Implementation

**Goal:** Add ERC-8004 reputation signals to x402 payments, enabling agents to build on-chain reputation from paid interactions.

**Architecture (all changes in `naive_implementation/`):**

```
                  ┌─────────────────────────────────────────────────┐
                  │              Anvil Fork (chain 1)               │
                  │  IdentityRegistry   ReputationRegistry          │
                  │  FeedbackGateway    USDC (mainnet)               │
                  └─────────────────────────────────────────────────┘
                         ▲   ▲    ▲                         
                         │   │    │                         
              ┌──────────┘   │    └──────────┐              
              ▼              ▼               ▼              
  ┌─────────────────┐ ┌──────────┐ ┌────────────────┐    
  │ Facilitator      │ │Resource  │ │ Client          │     
  │ (FastAPI :4022)  │ │Server    │ │ (httpx + x402)  │    
  │ verify/settle    │ │(:4021)   │ │ pay → proof     │    
  │                  │ │x402 pay  │ │ → feedback      │    
  └─────────────────┘ │+Rep       │ └────────────────┘    
                      │Middleware │                       
                      └──────────┘                       
```

## Flow

1. **Bootstrap** — fund client with mainnet USDC, deploy FeedbackGateway, register agent on ERC-8004 IdentityRegistry
2. **Pay** — client pays agent $0.01 USDC via x402 EIP-3009 (`transferWithAuthorization`)
3. **Sign** — `ReputationMiddleware` signs `keccak256(agentId || reqBody || respBody)` with agent's key → `X-Reputation-Proof`
4. **Feedback** — client submits feedback on-chain:
   - `FeedbackGateway.markUsed(hash)` — dedup (SSTORE first, retriable on failure)
   - `ReputationRegistry.giveFeedback(agentId, 95, 0, "x402", "weather", ...)` — on-chain reputation
5. **Dedup** — `markUsed` returns `false` for duplicate proof → feedback blocked

## Files

| File | Action |
|------|--------|
| `naive_implementation/main.py` | Single-file demo (all steps inline) |
| `naive_implementation/contracts/FeedbackGateway.sol` | On-chain dedup (`mapping(bytes32 => bool)`) |
| `naive_implementation/foundry.toml` | Solidity build config |
| `naive_implementation/.env` | Private keys |
| `docs/x402-erc8004-implementation.md` | This file |

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Dedup** | `FeedbackGateway.sol` (on-chain) | Client can fall back to direct submission if facilitator censors. Replaces Redis+Subgraph plan. |
| **Token** | Mainnet USDC (`0xA0b8…eB48`) on Anvil fork | EIP-3009 via x402 SDK + `eth_account`; facilitator transfers USDC to client at bootstrap. |
| **Agent Identity** | ERC-8004 IdentityRegistry | Real on-chain agent registration via `register()`. Requires fresh EOA (Anvil defaults have EIP-7702 delegation on mainnet). |
| **Signing** | `eth_account` ECDSA | Simple EIP-191 signed hash of `(agentId, reqBody, respBody)`. |
| **Middleware** | `ReputationMiddleware` wraps `PaymentMiddlewareASGI` | Outer ASGI middleware intercepts response after payment settlement. |
| **Feedback** | Client submits directly via `giveFeedback()` | Client uses own EOA. `msg.sender` is the feedback author. |

## Running

```bash
# Terminal 1: Start Anvil fork
anvil --fork-url <RPC_URL> --chain-id 1

# Terminal 2: Run demo
cd naive_implementation
uv run python main.py
```

## USDC on mainnet fork

Uses Circle mainnet USDC with domain `name=USD Coin`, `version=2`. The facilitator (Anvil account #0) must hold USDC on the fork; the script transfers 50 USDC to the client before payment. Set `PAYMENT_TOKEN=dai` to use DAI via Permit2 instead.
