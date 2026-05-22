"""One-time bootstrap: deploy FeedbackGateway, register agent, fund client."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import no_type_check

from eth_account import Account
from web3 import Web3

from src.shared.constants import (
    ROOT,
    RPC_URL,
    NETWORK,
    MAINNET_USDC_ADDRESS,
    DAI_ADDRESS,
    PERMIT2_ADDRESS,
    IDENTITY_REGISTRY,
    REPUTATION_REGISTRY,
    DEFAULT_FACILITATOR_KEY,
    FUND_USDC,
    FUND_DAI,
    ERC8004_CONTRACTS_ROOT,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("setup")


@dataclass(frozen=True)
class LocalSetup:
    rpc_url: str
    network: str
    usdc_address: str
    dai_address: str
    feedback_gateway: str
    agent_id: int
    facilitator_account: Account
    client_account: Account
    agent_account: Account


def require_anvil(rpc_url: str) -> Web3:
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
            f"Then re-run.\n\n"
            f"(tried RPC_URL={rpc_url}: {exc})\n"
        )
        sys.exit(1)
    if chain_id != 1:
        print(f"\nExpected chain id 1 (mainnet fork), got {chain_id}.\nStart Anvil with --chain-id 1.\n")
        sys.exit(1)
    return w3


def sync_anvil_clock(w3: Web3) -> None:
    target = int(time.time()) + 1
    latest_ts = w3.eth.get_block("latest")["timestamp"]
    if latest_ts < target:
        logger.info("Advancing Anvil block time %s -> %s", latest_ts, target)
        w3.provider.make_request("anvil_setNextBlockTimestamp", [target])
        w3.provider.make_request("evm_mine", [])


def generate_fresh_key() -> Account:
    acct = Account.create()
    logger.info("Generated fresh key at %s", acct.address)
    return acct


def load_feedback_gateway_artifact() -> dict:
    artifact_path = ROOT / "out" / "FeedbackGateway.sol" / "FeedbackGateway.json"
    if not artifact_path.exists():
        raise RuntimeError(f"Missing {artifact_path}. Run: cd {ROOT} && forge build")
    return json.loads(artifact_path.read_text())


def load_reputation_registry_abi() -> list:
    abi_path = ERC8004_CONTRACTS_ROOT / "abis" / "ReputationRegistry.json"
    if not abi_path.exists():
        raise RuntimeError(f"Missing {abi_path}. Run: cd {ROOT} && forge install erc-8004/erc-8004-contracts")
    return json.loads(abi_path.read_text())


def deploy_feedback_gateway(w3: Web3, deployer_key: str) -> str:
    artifact = load_feedback_gateway_artifact()
    deployer = Account.from_key(deployer_key)
    contract = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    tx = contract.constructor().build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 3_000_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = deployer.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    if not receipt.status or not receipt.contractAddress:
        raise RuntimeError(f"FeedbackGateway deployment failed: status={receipt.status}")
    addr = Web3.to_checksum_address(receipt.contractAddress)
    logger.info("Deployed FeedbackGateway at %s", addr)
    return addr


def register_agent(w3: Web3, agent_key: str) -> int:
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
    agent_id = int.from_bytes(receipt.logs[1].topics[1], "big")
    logger.info("Registered agent %s with agentId=%s", agent.address, agent_id)
    return agent_id


def bootstrap() -> LocalSetup:
    facilitator_key = os.getenv("FACILITATOR_PRIVATE_KEY", DEFAULT_FACILITATOR_KEY)
    client_key = os.getenv("CLIENT_PRIVATE_KEY")
    agent_key = os.getenv("AGENT_PRIVATE_KEY")

    w3 = require_anvil(RPC_URL)
    sync_anvil_clock(w3)
    facilitator_account = Account.from_key(facilitator_key)

    if client_key:
        client_account = Account.from_key(client_key)
        logger.info("Using client key from env at %s", client_account.address)
    else:
        client_account = generate_fresh_key()

    if agent_key:
        agent_account = Account.from_key(agent_key)
        logger.info("Using agent key from env at %s", agent_account.address)
    else:
        agent_account = generate_fresh_key()

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

    usdc_address = Web3.to_checksum_address(MAINNET_USDC_ADDRESS)
    erc20_abi = [
        {"inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
        {"inputs": [{"name": "who", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    ]
    usdc = w3.eth.contract(address=usdc_address, abi=erc20_abi)
    logger.info("Funding client with %s USDC from facilitator...", FUND_USDC / 10**6)
    tx = usdc.functions.transfer(client_account.address, FUND_USDC).build_transaction({
        "from": facilitator_account.address,
        "nonce": w3.eth.get_transaction_count(facilitator_account.address),
        "gas": 100_000,
        "maxFeePerGas": w3.to_wei(2, "gwei"),
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id,
    })
    signed = facilitator_account.sign_transaction(tx)
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    usdc_bal = usdc.functions.balanceOf(client_account.address).call()
    logger.info("Client USDC balance: %s", usdc_bal)

    logger.info("Funding client with 100 DAI from facilitator...")
    dai_addr = Web3.to_checksum_address(DAI_ADDRESS)
    dai = w3.eth.contract(address=dai_addr, abi=[
        {"inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
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

    permit2_addr = Web3.to_checksum_address(PERMIT2_ADDRESS)
    dai = w3.eth.contract(address=dai_addr, abi=[
        {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
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

    feedback_gateway = deploy_feedback_gateway(w3, facilitator_key)
    agent_id = register_agent(w3, agent_account.key.hex())

    dai_bal = w3.eth.contract(address=dai_addr, abi=[
        {"inputs": [{"name": "who", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    ]).functions.balanceOf(client_account.address).call()
    logger.info("Client DAI balance: %s", dai_bal)

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


def assert_feedback_client_address(w3: Web3, receipt, *, agent_id: int, client_address: str) -> None:
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(REPUTATION_REGISTRY),
        abi=load_reputation_registry_abi(),
    )
    client_checksum = Web3.to_checksum_address(client_address)
    events = registry.events.NewFeedback().process_receipt(receipt)
    matching = [e for e in events if e["args"]["agentId"] == agent_id]
    assert matching, f"No NewFeedback event for agentId={agent_id} in tx {receipt.transactionHash.hex()}"
    author = Web3.to_checksum_address(matching[-1]["args"]["clientAddress"])
    assert author == client_checksum, f"feedback clientAddress {author} != expected client EOA {client_checksum}"
    last_idx = registry.functions.getLastIndex(agent_id, client_checksum).call()
    assert last_idx > 0, f"getLastIndex(agentId={agent_id}, client) returned 0 after feedback"


if __name__ == "__main__":
    setup = bootstrap()
    print(f"  FeedbackGateway:  {setup.feedback_gateway}")
    print(f"  AgentId:          {setup.agent_id}")
    print(f"  Facilitator:      {setup.facilitator_account.address}")
    print(f"  Agent (pay):      {setup.agent_account.address}")
    print(f"  Client:           {setup.client_account.address}")
