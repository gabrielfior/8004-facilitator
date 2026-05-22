"""Test EIP-7702 delegation on any RPC (Anvil, Sepolia, etc.).

Deploys a small SenderChecker contract, signs an EIP-7702 authorization
delegating a test EOA to that contract, then sends a self-call.
If EIP-7702 works, the event's `caller` will be the test EOA.
If not, the tx succeeds silently (no code at EOA) and nothing is emitted.

Works on:
  - Anvil --hardfork prague (via send_raw_transaction)
  - Sepolia / Pectra-enabled chains (via send_raw_transaction)

Usage:
  PRIVATE_KEY_WITH_FUNDS_ON_SEPOLIA=0x... RPC_URL=<url> uv run python test_eip7702.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import rlp
from dotenv import load_dotenv

from eth_account import Account
from eth_hash.auto import keccak as _keccak
from eth_keys import keys
from web3 import Web3

# Load .env from repo root
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
SENDER_KEY_ENV = os.getenv(
    "PRIVATE_KEY_WITH_FUNDS_ON_SEPOLIA",
    os.getenv("FACILITATOR_PRIVATE_KEY", ""),
)

BYTECODE = "0x60808060405234601457608e90816100198239f35b5f80fdfe6004361015600b575f80fd5b5f803560e01c63919840ad14601e575f80fd5b346055578060031936011260555732337f664409437c3787d326d629bdd8447647a29c84a8d6814ee7d52cd931f84caf348380a380f35b80fdfea26469706673582212206b9bc15281073714332e2e6033be96afadb73e06dceb94f49f456316ce21c99964736f6c63430008140033"
ABI = [
    {"inputs": [], "name": "check", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"anonymous": False, "inputs": [{"indexed": True, "internalType": "address", "name": "msgSender", "type": "address"}, {"indexed": True, "internalType": "address", "name": "txOrigin", "type": "address"}], "name": "Caller", "type": "event"},
]


def _send_raw(w3: Web3, tx_dict: dict, key) -> dict:
    """Sign and send a raw transaction, return receipt."""
    signed = Account.sign_transaction(tx_dict, key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def main() -> None:
    if not SENDER_KEY_ENV:
        print("ERROR: Set PRIVATE_KEY_WITH_FUNDS_ON_SEPOLIA env var")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    chain_id = w3.eth.chain_id
    sender_acct = Account.from_key(SENDER_KEY_ENV)
    sender_addr = sender_acct.address

    print(f"RPC:        {RPC_URL}")
    print(f"Chain ID:   {chain_id}")
    print(f"Sender:     {sender_addr}")
    print(f"Balance:    {w3.from_wei(w3.eth.get_balance(sender_addr), 'ether')} ETH")
    print()

    # ---- Deploy SenderChecker ----
    gas_price = w3.eth.gas_price
    print(f"Gas price:  {w3.from_wei(gas_price, 'gwei')} gwei")

    checker = w3.eth.contract(abi=ABI, bytecode=BYTECODE)
    tx = checker.constructor().build_transaction({
        "from": sender_addr,
        "nonce": w3.eth.get_transaction_count(sender_addr),
        "gas": 200_000,
        "gasPrice": gas_price,
        "chainId": chain_id,
    })
    receipt = _send_raw(w3, tx, SENDER_KEY_ENV)
    checker_addr = Web3.to_checksum_address(receipt.contractAddress)
    print(f"Deployed    SenderChecker at {checker_addr}")

    # ---- Use deployer as the test subject (has ETH, no funding needed) ----
    test_eoa = sender_acct
    test_bal = w3.eth.get_balance(test_eoa.address)
    print(f"Test EOA:   {test_eoa.address}")
    print(f"Balance:    {w3.from_wei(test_bal, 'ether')} ETH")
    print(f"Code:       {len(w3.eth.get_code(test_eoa.address))} bytes")
    print()

    # ---- Build raw EIP-7702 type 0x04 transaction using deployer nonce ----
    tx_nonce = w3.eth.get_transaction_count(test_eoa.address)
    base_fee = w3.eth.get_block("pending").get("baseFeePerGas", gas_price)

    # Sign authorization (nonce = tx_nonce + 1 for self-call)
    auth = Account.sign_authorization(
        {"chainId": chain_id, "address": checker_addr, "nonce": tx_nonce + 1},
        test_eoa.key,
    )

    calldata = checker.encode_abi("check")
    to_bytes = bytes.fromhex(test_eoa.address[2:])
    checker_bytes = bytes.fromhex(checker_addr[2:])
    calldata_raw = bytes.fromhex(calldata[2:])

    max_priority = w3.to_wei(1, "gwei")
    max_fee = base_fee + max_priority
    gas_limit = 100_000

    # Build payload with placeholder signature, hash, sign, replace
    payload_placeholder = rlp.encode([
        chain_id, tx_nonce,
        max_priority, max_fee, gas_limit,
        to_bytes, 0, calldata_raw, [],
        [[chain_id, checker_bytes, tx_nonce + 1, auth.y_parity, auth.r, auth.s]],
        0, 0, 0,
    ])

    hash_to_sign = _keccak(bytes([4]) + payload_placeholder)
    pk = keys.PrivateKey(test_eoa.key)
    sig = pk.sign_msg_hash(hash_to_sign)
    y_parity_val = sig.v if sig.v <= 1 else sig.v - 27

    raw_tx = bytes([4]) + rlp.encode([
        chain_id, tx_nonce,
        max_priority, max_fee, gas_limit,
        to_bytes, 0, calldata_raw, [],
        [[chain_id, checker_bytes, tx_nonce + 1, auth.y_parity, auth.r, auth.s]],
        y_parity_val, sig.r, sig.s,
    ])

    # ---- Send EIP-7702 tx ----
    print("--- EIP-7702 test: raw signed type 0x04 ---")
    print(f"  max_fee:        {w3.from_wei(max_fee, 'gwei')} gwei")
    print(f"  max_priority:   {w3.from_wei(max_priority, 'gwei')} gwei")
    print(f"  gas:            {gas_limit}")
    print(f"  auth_nonce:     {tx_nonce + 1} (tx_nonce + 1)")
    print(f"  Raw tx length:  {len(raw_tx)} bytes")

    try:
        # Retry with backoff for RPC consistency
        tx_hash = None
        for attempt in range(10):
            try:
                tx_hash = w3.eth.send_raw_transaction(raw_tx)
                break
            except Exception as e:
                if "insufficient funds" in str(e) and attempt < 9:
                    print(f"  Retry {attempt + 1}/10 (RPC consistency)...")
                    time.sleep(2)
                    continue
                raise e

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"  Status:         {receipt.status}")
        print(f"  Gas used:       {receipt.gasUsed}")
        print(f"  Logs:           {len(receipt.logs)}")

        if receipt.logs:
            event = checker.events.Caller().process_receipt(receipt)
            if event:
                e = event[0]["args"]
                print(f"\n>>> EIP-7702 WORKS on this network! <<<")
                print(f"  msg.sender:     {e.msgSender}")
                print(f"  tx.origin:      {e.txOrigin}")
                print(f"  msg.sender == test EOA: {e.msgSender.lower() == test_eoa.address.lower()}")
        else:
            print(f"\n>>> EIP-7702 NOT supported <<<")
            print("  No events emitted — delegation was not applied")

    except Exception as exc:
        print(f"\n  Error: {exc}")
        print(f"\n>>> EIP-7702 NOT supported <<<")


if __name__ == "__main__":
    main()
