# x402 E2E Test on Ethereum Sepolia

This guide walks through completing an x402 payment transaction on Ethereum Sepolia using our facilitator.

## Architecture

```
┌─────────────┐     x402-protected      ┌──────────────┐     verify/settle     ┌────────────────┐
│  Python     │──────request──────────▶│  Resource    │───────payment────────▶│  Facilitator   │
│  Client     │                         │  Server      │                       │  (localhost:4022)│
│  (payer)    │◀───────resource────────│  (port 4021) │◀───────response──────│                │
└─────────────┘                         └──────────────┘                       └────────────────┘
```

## Prerequisites

- Node.js 18+
- Docker (for Redis)
- Python 3.10+
- Sepolia ETH for the client (get from a faucet)

## Step 1: Start Redis

```bash
docker run -d --name facilitator-redis -p 6379:6379 redis:7-alpine
```

Verify: `redis-cli ping` → `PONG`

## Step 2: Generate a Client Private Key

This is the payer's key (needs Sepolia ETH):

```bash
openssl rand -hex 32
```

Export it:
```bash
export CLIENT_PRIVATE_KEY=0x<your-hex-key>
```

## Step 3: Set Up and Start the Resource Server

Create `.env` for the resource server (`examples/v2-server/.env`):

```bash
FACILITATOR_URL=http://localhost:4022
ADDRESS=0x<agent-address>  # The agent's receiving address
AGENT_PRIVATE_KEY=0x<agent-private-key>
DELEGATE_CONTRACT_ADDRESS=0x252367B463f77EFe33c151E9d9821788090EC4b5
```

Then start it:
```bash
npm run dev
```

In a separate terminal, start the facilitator:
```bash
npm run dev
```

## Step 4: Register the Agent

The resource server needs to register with ERC-8004. Call:

```bash
curl -X POST http://localhost:4021/register-agent \
  -H "Content-Type: application/json" \
  -d '{"chainId": 11155111, "network": "eip155:11155111"}'
```

## Step 5: Set Up Python Environment

```bash
python3 -m venv /tmp/x402-venv
source /tmp/x402-venv/bin/activate
pip install x402[httpx,evm] python-dotenv web3
```

## Step 6: Run the Test

```bash
source /tmp/x402-venv/bin/activate
CLIENT_PRIVATE_KEY=0x<your-key> python e2e/test_x402_payment.py
```

## Expected Output

```
Client address: 0x...
Balance: 1.234000 ETH

Requesting http://localhost:4021/weather...
Status: 200
{
  "success": true,
  "status_code": 200,
  "data": {
    "report": {
      "weather": "sunny",
      "temperature": 70
    }
  },
  "payment_response": {
    "transaction": "0x...",
    "network": "eip155:11155111",
    ...
  }
}
```

The `payment_response.transaction` is the on-chain settlement tx hash on Sepolia.

## Troubleshooting

- **"Account has no ETH"** → Fund from https://www.alchemy.com/faucets/ethereum-sepolia
- **Agent not registered** → Complete Step 4 first
- **Connection refused** → Make sure facilitator and resource server are running
- **x402 SDK import errors** → Verify the venv is activated and `pip list | grep x402` shows it
