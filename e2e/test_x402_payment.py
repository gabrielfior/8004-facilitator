import os
import json
import asyncio
import logging
from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3

from x402 import x402Client
from x402.http import decode_payment_response_header
from x402.http.clients import x402_httpx_transport
from x402.mechanisms.evm import EthAccountSignerWithRPC
from x402.mechanisms.evm.exact import register_exact_evm_client

import httpx

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logging.getLogger("x402").setLevel(logging.DEBUG)

load_dotenv()

FACILITATOR_URL = os.getenv("FACILITATOR_URL", "http://localhost:4022")
CLIENT_PRIVATE_KEY = os.getenv("CLIENT_PRIVATE_KEY")
AGENT_PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://ethereum-sepolia-rpc.publicnode.com")
DELEGATE_CONTRACT = os.getenv("DELEGATE_CONTRACT_ADDRESS", "0x252367B463f77EFe33c151E9d9821788090EC4b5")
REGISTRY_CHAIN_ID = 11155111


async def register_agent(agent_key: str, http_client: httpx.AsyncClient) -> str | None:
    agent_account = Account.from_key(agent_key)
    print(f"Registering agent {agent_account.address}...")

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    nonce = w3.eth.get_transaction_count(agent_account.address)
    print(f"Agent nonce: {nonce}")

    auth = Account.sign_authorization(
        {
            "chainId": REGISTRY_CHAIN_ID,
            "address": DELEGATE_CONTRACT,
            "nonce": nonce,
        },
        agent_key,
    )

    address_hex = "0x" + auth.address.hex() if isinstance(auth.address, bytes) else auth.address
    auth_payload = {
        "chainId": str(auth.chain_id),
        "address": address_hex,
        "nonce": str(auth.nonce),
        "yParity": auth.y_parity,
        "r": hex(auth.r),
        "s": hex(auth.s),
    }

    resp = await http_client.post("/register", json={
        "agentAddress": agent_account.address,
        "authorization": auth_payload,
        "network": f"eip155:{REGISTRY_CHAIN_ID}",
        "x402Version": 1,
        "tokenURI": "https://example.com/agent-metadata",
        "metadata": [{"key": "name", "value": "Sepolia Test Agent"}],
    })

    result = resp.json()
    if result.get("success"):
        agent_id = result["agentId"]
        print(f"✅ Agent registered: agentId={agent_id}, txHash={result.get('txHash')}")
        return agent_id
    else:
        print(f"❌ Agent registration failed: {result.get('error')}")
        return None


async def submit_feedback(http_client: httpx.AsyncClient, agent_id: str, score: int, tx_hash: str):
    print(f"Submitting feedback for agent {agent_id}: score={score}...")
    resp = await http_client.post("/feedback", json={
        "agentId": agent_id,
        "score": score,
        "tag1": "starred",
        "tag2": "x402",
        "endpoint": "/weather",
        "network": f"eip155:{REGISTRY_CHAIN_ID}",
    })
    result = resp.json()
    if result.get("success"):
        print(f"✅ Feedback submitted! txHash={result['txHash']}")
    else:
        print(f"❌ Feedback failed: {result.get('error')}")
    return result


async def main():
    if not CLIENT_PRIVATE_KEY:
        print(json.dumps({"success": False, "error": "CLIENT_PRIVATE_KEY not set"}))
        return
    if not AGENT_PRIVATE_KEY:
        print(json.dumps({"success": False, "error": "AGENT_PRIVATE_KEY not set"}))
        return

    client_account = Account.from_key(CLIENT_PRIVATE_KEY)
    agent_account = Account.from_key(AGENT_PRIVATE_KEY)
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    balance = w3.eth.get_balance(client_account.address)
    print(f"Client address: {client_account.address}")
    print(f"Agent address:  {agent_account.address}")
    print(f"Client balance: {w3.from_wei(balance, 'ether'):.6f} ETH")

    if balance == 0:
        print("WARNING: Account has no ETH.")
        return

    # --- Step 1: x402 Payment ---
    client = x402Client()
    evm_signer = EthAccountSignerWithRPC(client_account, rpc_url=RPC_URL)
    register_exact_evm_client(client, evm_signer)

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(
        base_url="http://localhost:4021",
        timeout=timeout,
        transport=x402_httpx_transport(client),
    ) as resource_client:
        print(f"\n--- Step 1: x402 Payment ---")
        response = await resource_client.get("/weather")
        content = response.content
        result = {
            "success": True,
            "status_code": response.status_code,
            "data": json.loads(content.decode()),
        }
        payment_header = response.headers.get("PAYMENT-RESPONSE") or response.headers.get("X-PAYMENT-RESPONSE")
        payment_response = None
        tx_hash = None
        if payment_header:
            payment_response = decode_payment_response_header(payment_header)
            result["payment_response"] = payment_response.model_dump()
            tx_hash = payment_response.transaction
            print(f"Payment settled! txHash={tx_hash}")
        else:
            print("No payment response header found")

        print(json.dumps(result, indent=2))

    # --- Step 2: Register Agent with ERC-8004 ---
    print(f"\n--- Step 2: Register Agent ---")
    async with httpx.AsyncClient(base_url=FACILITATOR_URL, timeout=timeout) as fac_client:
        agent_id = await register_agent(AGENT_PRIVATE_KEY, fac_client)
        if not agent_id:
            print("Skipping feedback — agent registration failed")
            return

        # --- Step 3: Submit Positive Review ---
        print(f"\n--- Step 3: Submit Feedback (score=95) ---")
        fb_result = await submit_feedback(fac_client, agent_id, 95, tx_hash or "")
        if fb_result.get("success"):
            print(f"\n✅ Review submitted on-chain!")
            print(f"   Sepolia Etherscan: https://sepolia.etherscan.io/tx/{fb_result['txHash']}")


if __name__ == "__main__":
    asyncio.run(main())
