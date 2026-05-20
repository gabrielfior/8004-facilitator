"""x402 + ERC-8004 demo: facilitator + resource server + paying client + reputation.

Uses the Coinbase x402 Python SDK (client, server middleware, facilitator)
and real ERC-8004 contracts on a mainnet fork.

Prerequisites:
  1. Start an Anvil mainnet fork:
       anvil --fork-url <RPC_URL> --chain-id 1
  2. Run this script:
       uv run python main.py

Optional env:
  RPC_URL=http://127.0.0.1:8545
  FACILITATOR_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80  # Anvil #0 (has USDC)
  CLIENT_PRIVATE_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d  # Anvil #1
  AGENT_PRIVATE_KEY=<optional, fresh key generated if unset>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import uvicorn
from eth_account import Account
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from web3 import Web3

from x402 import x402Client, x402Facilitator, x402ResourceServer
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.clients import x402_httpx_transport
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.http.x402_http_client import x402HTTPClient
from x402.mechanisms.evm import EthAccountSignerWithRPC, FacilitatorWeb3Signer
from x402.mechanisms.evm.constants import NETWORK_CONFIGS
from x402.mechanisms.evm.exact import (
    ExactEvmServerScheme,
    register_exact_evm_client,
    register_exact_evm_facilitator,
)
from x402.schemas import AssetAmount, Network, PaymentRequirements, parse_payment_payload

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Reputation proof signing
# ---------------------------------------------------------------------------

def _proof_hash(agent_id: int, req_body: bytes, resp_body: bytes) -> bytes:
    return Web3.solidity_keccak(
        ["uint256", "bytes", "bytes"],
        [agent_id, req_body, resp_body]
    )

def _sign_proof(agent_key: str, agent_id: int, req_body: bytes, resp_body: bytes) -> str:
    from eth_account.messages import encode_defunct
    acct = Account.from_key(agent_key)
    h = _proof_hash(agent_id, req_body, resp_body)
    signed = Account.sign_message(encode_defunct(h), acct.key)
    return signed.signature.hex()

def _verify_proof(signer: str, agent_id: int, req_body: bytes, resp_body: bytes, sig: str) -> bool:
    from eth_account.messages import encode_defunct
    h = _proof_hash(agent_id, req_body, resp_body)
    recovered = Account.recover_message(encode_defunct(h), signature=bytes.fromhex(sig.removeprefix("0x")))
    return recovered.lower() == signer.lower()


class ReputationMiddleware(BaseHTTPMiddleware):
    """Outer middleware that signs request/response proof after x402 payment.

    Must be registered AFTER PaymentMiddlewareASGI so it wraps the payment flow.
    """

    def __init__(self, app, agent_key: str, agent_id: int):
        self.agent_key = agent_key
        self.agent_id = agent_id
        super().__init__(app)

    async def dispatch(self, request, call_next):
        req_body = await request.body()
        response = await call_next(request)

        if response.status_code != 200:
            return response

        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk

        proof = _sign_proof(self.agent_key, self.agent_id, req_body, resp_body)
        headers = dict(response.headers)
        headers["X-Reputation-Proof"] = proof
        return Response(content=resp_body, status_code=200, headers=headers, media_type=response.media_type)

# Monkey-patch: add Ethereum mainnet config for MockUSDC + DAI
NETWORK_CONFIGS["eip155:1"] = {
    "chain_id": 1,
    "default_asset": {
        "address": "0x0000000000000000000000000000000000000000",  # placeholder
        "name": "USD Coin",
        "version": "2",
        "decimals": 6,
    },
    "supported_assets": {
        "DAI": {
            "address": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
            "name": "Dai Stablecoin",
            "version": "1",
            "decimals": 18,
            "asset_transfer_method": "permit2",
            "supports_eip2612": True,
        },
    },
}

ROOT = Path(__file__).resolve().parent
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
NETWORK: Network = "eip155:1"
FACILITATOR_PORT = int(os.getenv("FACILITATOR_PORT", "4022"))
SERVER_PORT = int(os.getenv("SERVER_PORT", "4021"))
FACILITATOR_URL = os.getenv("FACILITATOR_URL", f"http://127.0.0.1:{FACILITATOR_PORT}")
SERVER_URL = os.getenv("SERVER_URL", f"http://127.0.0.1:{SERVER_PORT}")

# Anvil dev accounts (default mnemonic: "test test ... junk")
# Anvil #0 has 10000 ETH + USDC on the mainnet fork
DEFAULT_FACILITATOR_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_CLIENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
# Agent gets a fresh key (Anvil defaults have EIP-7702 delegation on mainnet)

# Deployed MockUSDC (EIP-3009 compatible, SDK-tested)
USDC_ADDRESS = None  # Set after deployment
DAI_ADDRESS = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

# ERC-8004 contract addresses
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPUTATION_REGISTRY = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"

PRICE_AMOUNT = "10000"  # $0.01 USDC (6 decimals)
FUND_USDC = 50_000_000  # 50 USDC to fund the client
FUND_DAI = 100 * 10**18  # 100 DAI (18 decimals)
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN", "dai").lower()  # "usdc" or "dai"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("naive_x402")


@dataclass(frozen=True)
class LocalSetup:
    rpc_url: str
    network: Network
    usdc_address: str
    dai_address: str
    feedback_gateway: str
    agent_id: int
    facilitator_account: Account
    client_account: Account
    agent_account: Account


def _require_anvil(rpc_url: str) -> Web3:
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 3}))
        if not w3.is_connected():
            raise ConnectionError("RPC not reachable")
        chain_id = w3.eth.chain_id
    except Exception as exc:
        print(
            "\nCannot reach a local EVM node.\n"
            "Start a mainnet Anvil fork first:\n\n"
            "  anvil --fork-url <RPC_URL> --chain-id 1\n\n"
            f"Then re-run: uv run python main.py\n\n"
            f"(tried RPC_URL={rpc_url}: {exc})\n"
        )
        sys.exit(1)

    if chain_id != 1:
        print(
            f"\nExpected chain id 1 (mainnet fork), got {chain_id}.\n"
            "Start Anvil with --chain-id 1.\n"
        )
        sys.exit(1)
    return w3


def _generate_fresh_key() -> Account:
    acct = Account.create()
    logger.info("Generated fresh key at %s", acct.address)
    return acct


def _deploy_feedback_gateway(w3: Web3, deployer_key: str) -> str:
    artifact_path = ROOT / "out" / "FeedbackGateway.sol" / "FeedbackGateway.json"
    if not artifact_path.exists():
        raise RuntimeError(f"Missing {artifact_path}. Run: cd {ROOT} && forge build --via-ir --optimize --optimizer-runs 200")
    artifact = json.loads(artifact_path.read_text())
    deployer = Account.from_key(deployer_key)
    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    tx = contract.constructor().build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 200_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = deployer.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    addr = Web3.to_checksum_address(receipt.contractAddress)
    logger.info("Deployed FeedbackGateway at %s", addr)
    return addr


def _register_agent(w3: Web3, agent_key: str) -> int:
    agent = Account.from_key(agent_key)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(IDENTITY_REGISTRY),
        abi=[{
            "inputs": [],
            "name": "register",
            "outputs": [{"name": "agentId", "type": "uint256"}],
            "stateMutability": "nonpayable",
            "type": "function",
        }],
    )
    tx = contract.functions.register().build_transaction({
        "from": agent.address,
        "nonce": w3.eth.get_transaction_count(agent.address),
        "gas": 300_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = agent.sign_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    # agentId from Registered event (log[1].topics[1])
    agent_id = int.from_bytes(receipt.logs[1].topics[1], "big")
    logger.info("Registered agent %s with agentId=%s", agent.address, agent_id)
    return agent_id


def bootstrap() -> LocalSetup:
    facilitator_key = os.getenv("FACILITATOR_PRIVATE_KEY", DEFAULT_FACILITATOR_KEY)
    client_key = os.getenv("CLIENT_PRIVATE_KEY")
    agent_key = os.getenv("AGENT_PRIVATE_KEY")

    w3 = _require_anvil(RPC_URL)
    facilitator_account = Account.from_key(facilitator_key)

    # Client gets a fresh key (Anvil defaults have EIP-7702 delegation on mainnet)
    if client_key:
        client_account = Account.from_key(client_key)
        logger.info("Using client key from env at %s", client_account.address)
    else:
        client_account = _generate_fresh_key()

    # Agent key from env, or generate fresh
    if agent_key:
        agent_account = Account.from_key(agent_key)
        logger.info("Using agent key from env at %s", agent_account.address)
    else:
        agent_account = _generate_fresh_key()

    # Fund both with ETH from facilitator
    for acct in [client_account, agent_account]:
        logger.info("Funding %s with 10 ETH", acct.address)
        tx_hash = w3.eth.send_transaction({
            "from": facilitator_account.address,
            "to": acct.address,
            "value": w3.to_wei(10, "ether"),
            "gas": 21_000,
            "maxFeePerGas": w3.to_wei(2, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
            "chainId": w3.eth.chain_id,
            "nonce": w3.eth.get_transaction_count(facilitator_account.address),
        })
        w3.eth.wait_for_transaction_receipt(tx_hash)

    # Deploy MockUSDC (EIP-3009 compatible, SDK-tested on this chain)
    logger.info("Deploying MockUSDC...")
    artifact_path = ROOT / "out" / "MockUSDC.sol" / "MockUSDC.json"
    if not artifact_path.exists():
        raise RuntimeError(f"Missing {artifact_path}. Run: cd {ROOT} && forge build --via-ir --optimize --optimizer-runs 200")
    artifact = json.loads(artifact_path.read_text())
    mock_usdc_contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    tx = mock_usdc_contract.constructor("USD Coin", "2", 6).build_transaction({
        "from": facilitator_account.address,
        "nonce": w3.eth.get_transaction_count(facilitator_account.address),
        "gas": 2_000_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = facilitator_account.sign_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    usdc_address = Web3.to_checksum_address(receipt.contractAddress)
    NETWORK_CONFIGS["eip155:1"]["default_asset"]["address"] = usdc_address
    logger.info("Deployed MockUSDC at %s", usdc_address)

    # Mint USDC to client
    mock_usdc = w3.eth.contract(address=usdc_address, abi=[
        {"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"mint","outputs":[],"stateMutability":"nonpayable","type":"function"}
    ])
    tx = mock_usdc.functions.mint(client_account.address, FUND_USDC).build_transaction({
        "from": facilitator_account.address,
        "nonce": w3.eth.get_transaction_count(facilitator_account.address),
        "gas": 100_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = facilitator_account.sign_transaction(tx)
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    logger.info("Minted 50 MockUSDC to client %s", client_account.address)

    # Fund client with DAI from facilitator (who has 1000 DAI via Tenderly)
    logger.info("Funding client with 100 DAI from facilitator...")
    dai_addr = Web3.to_checksum_address(DAI_ADDRESS)
    dai = w3.eth.contract(address=dai_addr, abi=[
        {"inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}
    ])
    tx = dai.functions.transfer(client_account.address, FUND_DAI).build_transaction({
        "from": facilitator_account.address,
        "nonce": w3.eth.get_transaction_count(facilitator_account.address),
        "gas": 100_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = facilitator_account.sign_transaction(tx)
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    dai_bal = w3.eth.contract(address=dai_addr, abi=[
        {"inputs":[{"name":"who","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
    ]).functions.balanceOf(client_account.address).call()
    logger.info("Client DAI balance: %s", dai_bal)

    # Approve Permit2 contract to spend client's DAI
    permit2_addr = Web3.to_checksum_address(PERMIT2_ADDRESS)
    dai = w3.eth.contract(address=dai_addr, abi=[
        {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"}
    ])
    tx = dai.functions.approve(permit2_addr, 2**256 - 1).build_transaction({
        "from": client_account.address,
        "nonce": w3.eth.get_transaction_count(client_account.address),
        "gas": 100_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = client_account.sign_transaction(tx)
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    logger.info("Approved Permit2 for client's DAI")

    # Deploy FeedbackGateway
    feedback_gateway = _deploy_feedback_gateway(w3, facilitator_key)

    # Register agent on ERC-8004 IdentityRegistry
    agent_id = _register_agent(w3, agent_account.key.hex())

    return LocalSetup(
        rpc_url=RPC_URL,
        network=NETWORK,
        usdc_address=usdc_address,
        dai_address=dai_addr,
        feedback_gateway=feedback_gateway,
        agent_id=agent_id,
        facilitator_account=facilitator_account,
        client_account=client_account,
        agent_account=agent_account,
    )


# ---------------------------------------------------------------------------
# Facilitator (FastAPI + x402Facilitator)
# ---------------------------------------------------------------------------

_facilitator_core: x402Facilitator | None = None


def _get_facilitator_core(setup: LocalSetup) -> x402Facilitator:
    global _facilitator_core
    if _facilitator_core is None:
        evm_signer = FacilitatorWeb3Signer(
            private_key=setup.facilitator_account.key.hex(),
            rpc_url=setup.rpc_url,
        )
        core = x402Facilitator()
        register_exact_evm_facilitator(core, evm_signer, networks=setup.network)
        _facilitator_core = core
    return _facilitator_core


class VerifyRequest(BaseModel):
    paymentPayload: dict
    paymentRequirements: dict


class SettleRequest(BaseModel):
    paymentPayload: dict
    paymentRequirements: dict


def create_facilitator_app(setup: LocalSetup) -> FastAPI:
    facilitator = _get_facilitator_core(setup)
    app = FastAPI(title="x402 Facilitator (local)", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/verify")
    async def verify(request: VerifyRequest) -> dict:
        try:
            payload = parse_payment_payload(request.paymentPayload)
            requirements = PaymentRequirements.model_validate(request.paymentRequirements)
            response = await facilitator.verify(payload, requirements)
            return response.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            logger.exception("verify failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/settle")
    async def settle(request: SettleRequest) -> dict:
        try:
            payload = parse_payment_payload(request.paymentPayload)
            requirements = PaymentRequirements.model_validate(request.paymentRequirements)
            response = await facilitator.settle(payload, requirements)
            return response.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            logger.exception("settle failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/supported")
    async def supported() -> dict:
        response = facilitator.get_supported()
        return {
            "kinds": [k.model_dump(by_alias=True, exclude_none=True) for k in response.kinds],
            "extensions": response.extensions,
            "signers": response.signers,
        }

    return app


# ---------------------------------------------------------------------------
# Resource server (FastAPI + x402 payment middleware)
# ---------------------------------------------------------------------------

def create_resource_server_app(setup: LocalSetup) -> FastAPI:
    facilitator_client = HTTPFacilitatorClient(FacilitatorConfig(url=FACILITATOR_URL))
    resource_server = x402ResourceServer(facilitator_client)
    resource_server.register(setup.network, ExactEvmServerScheme())
    resource_server.initialize()

    dai_price = str(int(int(PRICE_AMOUNT) * (10**18) / (10**6))) if setup.dai_address else PRICE_AMOUNT  # $0.01 DAI (18 decimals)

    usdc_option = PaymentOption(
        scheme="exact",
        pay_to=setup.agent_account.address,
        price=AssetAmount(
            amount=PRICE_AMOUNT,
            asset=setup.usdc_address,
            extra={"name": "USD Coin", "version": "2"},
        ),
        network=setup.network,
    )
    dai_option = PaymentOption(
        scheme="exact",
        pay_to=setup.agent_account.address,
        price=AssetAmount(
            amount=dai_price,
            asset=setup.dai_address,
            extra={"name": "Dai Stablecoin", "version": "1", "assetTransferMethod": "permit2"},
        ),
        network=setup.network,
        max_timeout_seconds=1800,
    )

    if PAYMENT_TOKEN == "usdc":
        accepts = [usdc_option]
    elif PAYMENT_TOKEN == "dai":
        accepts = [dai_option]
    else:
        accepts = [usdc_option, dai_option]

    routes = {
        "GET /weather": RouteConfig(
            accepts=accepts,
            mime_type="application/json",
            description="Paid weather report",
        ),
    }

    app = FastAPI(title="x402 Resource Server (local)", version="0.1.0")
    # Order: ReputationMiddleware (outer) wraps PaymentMiddlewareASGI (inner)
    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=resource_server)
    app.add_middleware(ReputationMiddleware, agent_key=setup.agent_account.key.hex(), agent_id=setup.agent_id)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/weather")
    async def weather() -> dict:
        return {
            "report": {"weather": "sunny", "temperature": 72},
        }

    return app


def _run_uvicorn(app: FastAPI, port: int) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


def _start_background_server(app: FastAPI, port: int) -> threading.Thread:
    thread = threading.Thread(target=_run_uvicorn, args=(app, port), daemon=True)
    thread.start()
    return thread


def _wait_for_http(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{url}/health", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Service did not become ready: {url}/health")


# ---------------------------------------------------------------------------
# Client (x402Client + httpx transport)
# ---------------------------------------------------------------------------

def _submit_feedback(
    w3: Web3,
    client_key: str,
    feedback_gateway: str,
    reputation_registry: str,
    agent_id: int,
    proof_hex: str,
    req_body: bytes,
    resp_body: bytes,
) -> bool:
    feedback_hash = Web3.solidity_keccak(
        ["uint256", "bytes", "bytes", "bytes"],
        [agent_id, req_body, resp_body, bytes.fromhex(proof_hex.removeprefix("0x"))]
    )

    # 1. Mark hash on FeedbackGateway
    gateway = w3.eth.contract(address=Web3.to_checksum_address(feedback_gateway), abi=[
        {"inputs":[{"name":"hash","type":"bytes32"}],"name":"markUsed","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
        {"inputs":[{"name":"hash","type":"bytes32"}],"name":"unmarkUsed","outputs":[],"stateMutability":"nonpayable","type":"function"},
    ])
    client_acct = Account.from_key(client_key)
    nonce = w3.eth.get_transaction_count(client_acct.address)

    is_new = gateway.functions.markUsed(feedback_hash).call({"from": client_acct.address})
    if not is_new:
        logger.warning("Feedback hash already used — duplicate feedback blocked")
        return False

    tx = gateway.functions.markUsed(feedback_hash).build_transaction({
        "from": client_acct.address, "nonce": nonce, "gas": 100_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = client_acct.sign_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    if not receipt.status:
        return False
    logger.info("FeedbackGateway.markUsed: status=%s", receipt.status)

    # 2. Submit feedback to ReputationRegistry
    registry = w3.eth.contract(address=Web3.to_checksum_address(reputation_registry), abi=[
        {"inputs":[{"name":"agentId","type":"uint256"},{"name":"value","type":"int128"},{"name":"valueDecimals","type":"uint8"},{"name":"tag1","type":"string"},{"name":"tag2","type":"string"},{"name":"endpoint","type":"string"},{"name":"feedbackURI","type":"string"},{"name":"feedbackHash","type":"bytes32"}],"name":"giveFeedback","outputs":[],"stateMutability":"nonpayable","type":"function"},
    ])
    nonce += 1
    try:
        tx2 = registry.functions.giveFeedback(
            agent_id, 95, 0, "x402", "weather", SERVER_URL, "", feedback_hash
        ).build_transaction({
            "from": client_acct.address, "nonce": nonce, "gas": 300_000,
            "maxFeePerGas": w3.to_wei(2, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
            "chainId": w3.eth.chain_id,
        })
        signed2 = client_acct.sign_transaction(tx2)
        receipt2 = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed2.raw_transaction))
        logger.info("ReputationRegistry.giveFeedback: status=%s", receipt2.status)
        return receipt2.status
    except Exception as exc:
        logger.error("giveFeedback failed: %s", exc)
        # Unmark on failure
        tx3 = gateway.functions.unmarkUsed(feedback_hash).build_transaction({
            "from": client_acct.address, "nonce": nonce, "gas": 50_000,
            "maxFeePerGas": w3.to_wei(2, "gwei"), "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
            "chainId": w3.eth.chain_id,
        })
        signed3 = client_acct.sign_transaction(tx3)
        w3.eth.send_raw_transaction(signed3.raw_transaction)
        return False


async def run_paying_client(setup: LocalSetup) -> None:
    client = x402Client()
    signer = EthAccountSignerWithRPC(setup.client_account, rpc_url=setup.rpc_url)
    register_exact_evm_client(client, signer, networks=setup.network)

    http_helper = x402HTTPClient(client)
    timeout = httpx.Timeout(60.0, connect=10.0)

    logger.info("Client %s paying agent %s via %s/weather", signer.address, setup.agent_account.address, SERVER_URL)

    w3 = Web3(Web3.HTTPProvider(setup.rpc_url))

    async with httpx.AsyncClient(
        base_url=SERVER_URL,
        timeout=timeout,
        transport=x402_httpx_transport(client),
    ) as http:
        response = await http.get("/weather")
        body = response.json()

    print("\n--- Payment result ---")
    print(f"HTTP {response.status_code}")
    print(json.dumps(body, indent=2))
    proof = response.headers.get("X-Reputation-Proof", "")
    if proof:
        print(f"\n--- Reputation proof ---")
        print(f"  X-Reputation-Proof: {proof[:64]}...")

        # Submit feedback on-chain
        print("\n--- Submitting feedback ---")
        ok = _submit_feedback(
            w3, setup.client_account.key.hex(),
            setup.feedback_gateway, REPUTATION_REGISTRY,
            setup.agent_id, proof, b"", json.dumps(body).encode(),
        )
        print(f"  Feedback submitted: {'YES' if ok else 'FAILED'}")

    try:
        settle = http_helper.get_payment_settle_response(lambda name: response.headers.get(name))
        print("\n--- Settlement ---")
        print(json.dumps(settle.model_dump(by_alias=True, exclude_none=True), indent=2))
    except ValueError:
        print("\nNo PAYMENT-RESPONSE header (payment may not have settled).")


async def main_async() -> None:
    setup = bootstrap()

    print("\nx402 + ERC-8004 stack")
    print(f"  RPC:              {setup.rpc_url}")
    print(f"  Network:          {setup.network}")
    print(f"  USDC:             {setup.usdc_address}")
    print(f"  DAI:              {setup.dai_address}")
    print(f"  FeedbackGateway:  {setup.feedback_gateway}")
    print(f"  IdentityRegistry: {IDENTITY_REGISTRY}")
    print(f"  ReputationReg:    {REPUTATION_REGISTRY}")
    print(f"  AgentId:          {setup.agent_id}")
    print(f"  Facilitator:      {setup.facilitator_account.address}  -> :{FACILITATOR_PORT}")
    print(f"  Agent (pay):      {setup.agent_account.address}")
    print(f"  Client:           {setup.client_account.address}")
    print(f"  Tokens:           USDC (EIP-3009) + DAI (Permit2)")
    print()

    facilitator_app = create_facilitator_app(setup)
    _start_background_server(facilitator_app, FACILITATOR_PORT)
    _wait_for_http(FACILITATOR_URL)

    server_app = create_resource_server_app(setup)
    _start_background_server(server_app, SERVER_PORT)
    _wait_for_http(SERVER_URL)
    logger.info("Facilitator and resource server are up")

    await run_paying_client(setup)

    print("\nDone. Facilitator and server stop when this process exits.\n")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
