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
RESOURCE_URL = os.getenv("RESOURCE_URL", "http://localhost:4021/weather")
CLIENT_PRIVATE_KEY = os.getenv("CLIENT_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL", "https://ethereum-sepolia-rpc.publicnode.com")


async def main():
    if not CLIENT_PRIVATE_KEY:
        print(json.dumps({"success": False, "error": "CLIENT_PRIVATE_KEY not set. Create one with: openssl rand -hex 32"}))
        return

    client_account = Account.from_key(CLIENT_PRIVATE_KEY)
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    balance = w3.eth.get_balance(client_account.address)
    print(f"Client address: {client_account.address}")
    print(f"Balance: {w3.from_wei(balance, 'ether'):.6f} ETH")

    if balance == 0:
        print("WARNING: Account has no ETH. Fund it from a Sepolia faucet first.")
        return

    client = x402Client()
    evm_signer = EthAccountSignerWithRPC(client_account, rpc_url=RPC_URL)
    register_exact_evm_client(client, evm_signer)

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(
        base_url="http://localhost:4021",
        timeout=timeout,
        transport=x402_httpx_transport(client),
    ) as http_client:
        try:
            print(f"\nRequesting {RESOURCE_URL}...")
            response = await http_client.get("/weather")

            content = response.content
            result = {
                "success": True,
                "status_code": response.status_code,
                "data": json.loads(content.decode()),
            }

            payment_header = response.headers.get("PAYMENT-RESPONSE") or response.headers.get("X-PAYMENT-RESPONSE")
            if payment_header:
                payment_response = decode_payment_response_header(payment_header)
                result["payment_response"] = payment_response.model_dump()

            print(json.dumps(result, indent=2))

        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
