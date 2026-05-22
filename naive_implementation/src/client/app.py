"""Client: pay via x402, poll settlement, submit feedback via EIP-7702."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3

from x402 import x402Client
from x402.http.clients import x402_httpx_transport
from x402.http.x402_http_client import x402HTTPClient
from x402.mechanisms.evm import EthAccountSignerWithRPC
from x402.mechanisms.evm.exact import register_exact_evm_client

from src.shared.constants import (
    RPC_URL,
    NETWORK,
    SERVER_URL,
    REPUTATION_REGISTRY,
    ROOT,
    ensure_network_config,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("client")

# EIP-7702 is supported on Sepolia (Pectra) but not on local Anvil.
# On Sepolia: EIP_7702_SUPPORTED=true → client self-call preserves msg.sender
# On Anvil: EIP_7702_SUPPORTED=false → direct call to gateway (registry sees gateway as caller)
EIP_7702_SUPPORTED = os.getenv("EIP_7702_SUPPORTED", "").lower() in ("1", "true", "yes")


def load_gateway_artifact() -> dict:
    path = ROOT / "out" / "FeedbackGateway.sol" / "FeedbackGateway.json"
    if not path.exists():
        raise RuntimeError(f"Missing {path}. Run: cd {ROOT} && forge build")
    return json.loads(path.read_text())


def poll_settlement_payer(
    w3: Web3,
    gateway: Web3,
    tx_hash: bytes,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payer = gateway.functions.settlementPayer(tx_hash).call()
        if payer != "0x0000000000000000000000000000000000000000":
            return True
        time.sleep(interval)
    return False


async def run_paying_client(
    client_key: str,
    feedback_gateway: str,
    agent_id: int,
) -> bool:
    client_acct = Account.from_key(client_key)
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    gateway_artifact = load_gateway_artifact()
    gateway = w3.eth.contract(
        address=Web3.to_checksum_address(feedback_gateway),
        abi=gateway_artifact["abi"],
    )

    ensure_network_config()

    client = x402Client()
    signer = EthAccountSignerWithRPC(client_acct, rpc_url=RPC_URL)
    register_exact_evm_client(client, signer, networks=NETWORK)
    http_helper = x402HTTPClient(client)

    logger.info("Client %s paying via %s/weather", signer.address, SERVER_URL)

    import httpx
    timeout_cfg = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(
        base_url=SERVER_URL,
        timeout=timeout_cfg,
        transport=x402_httpx_transport(client),
    ) as http:
        response = await http.get("/weather")

    if response.status_code != 200:
        logger.error("Request failed: HTTP %s %s", response.status_code, response.text[:200])
        return False

    body = response.json()
    logger.info("Got response: %s", json.dumps(body))

    try:
        settle = http_helper.get_payment_settle_response(
            lambda name: response.headers.get(name)
        )
    except ValueError as exc:
        logger.error("No PAYMENT-RESPONSE header: %s", exc)
        return False

    tx_hash = Web3.to_bytes(hexstr=settle.transaction)
    logger.info("Settlement txHash: %s...", tx_hash.hex()[:16])

    logger.info("Polling settlementPayer[%s...]...", tx_hash.hex()[:16])
    if not poll_settlement_payer(w3, gateway, tx_hash):
        logger.warning("Timeout waiting for settlement payer — falling back to unverified")
        settlement_tx_hash = bytes(32)
        is_verified = False
    else:
        settlement_tx_hash = tx_hash
        is_verified = True

    feedback_hash = Web3.solidity_keccak(
        ["uint256", "bytes32"],
        [agent_id, settlement_tx_hash],
    )

    if gateway.functions.hasBeenUsed(feedback_hash).call():
        logger.warning("Feedback hash already used — duplicate blocked")
        return False

    params_tuple = (
        agent_id,
        95,             # value
        0,              # valueDecimals
        "x402",
        "weather",
        SERVER_URL,
        "",
        feedback_hash,
    )
    calldata = gateway.encode_abi(
        "submitFeedback",
        args=[Web3.to_checksum_address(REPUTATION_REGISTRY), params_tuple, settlement_tx_hash],
    )

    gateway_addr = Web3.to_checksum_address(feedback_gateway)

    if EIP_7702_SUPPORTED:
        tx_nonce = w3.eth.get_transaction_count(client_acct.address)
        base_fee = w3.eth.get_block("pending").get("baseFeePerGas", w3.eth.gas_price)
        max_priority = w3.to_wei(1, "gwei")
        max_fee = base_fee + max_priority
        gas_limit = 100_000

        authorization = Account.sign_authorization(
            {"chainId": w3.eth.chain_id, "address": gateway_addr, "nonce": tx_nonce + 1},
            client_key,
        )

        tx = {
            "type": 4,
            "chainId": w3.eth.chain_id,
            "nonce": tx_nonce,
            "to": client_acct.address,
            "value": 0,
            "gas": gas_limit,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": max_priority,
            "data": calldata,
            "accessList": (),
            "authorizationList": [authorization],
        }

        mode = "EIP-7702"
        logger.info("submitFeedback (mode=%s) gas=%s", mode, gas_limit)
        signed = Account.sign_transaction(tx, client_key)
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(signed.raw_transaction)
        )

    logger.info(
        "%s submitFeedback: status=%s gasUsed=%s",
        mode, receipt.status, receipt.gasUsed,
    )

    if not receipt.status:
        logger.error("submitFeedback reverted")
        return False

    assert gateway.functions.hasBeenUsed(feedback_hash).call(), "feedback hash not consumed"
    if is_verified:
        assert gateway.functions.usedSettlements(tx_hash).call(), "settlement not marked used"

    logger.info("Feedback submitted: verified=%s agentId=%s", is_verified, agent_id)
    return True
