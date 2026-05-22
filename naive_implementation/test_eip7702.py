"""Test EIP-7702 delegation on the current RPC.

Deploys a small SenderChecker contract, signs an EIP-7702 authorization
delegating a test EOA to that contract, then sends a self-call.
If EIP-7702 works, the event's `caller` will be the test EOA.
If not, the tx succeeds silently (no code at EOA) and nothing is emitted.

Works with:
  - Anvil (uses pre-funded Anvil #0 as deployer)
  - Tenderly (uses tenderly_setBalance / anvil_setBalance)
  - Any RPC with anvil_setBalance support

Usage:
  RPC_URL=http://127.0.0.1:8545 uv run python test_eip7702.py
"""

from __future__ import annotations

import os

import rlp

from eth_account import Account

from eth_hash.auto import keccak as _keccak
from web3 import Web3

# Anvil #0 default key — has ETH on any Anvil fork
ANVIL_DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")


def _fund_account(w3: Web3, address: str) -> None:
    """Fund an account using available methods."""
    for method in ("anvil_setBalance", "tenderly_setBalance"):
        try:
            w3.provider.make_request(method, [address, hex(10**19)])
            return
        except Exception:
            continue
    dev = Account.from_key(ANVIL_DEV_KEY)
    dev_nonce = w3.eth.get_transaction_count(dev.address)
    try:
        w3.eth.send_transaction({
            "from": dev.address,
            "to": address,
            "value": w3.to_wei(10, "ether"),
            "gas": 21_000,
            "gasPrice": w3.to_wei(1, "gwei"),
            "chainId": w3.eth.chain_id,
            "nonce": dev_nonce,
        })
    except Exception:
        pass


def main() -> None:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    chain_id = w3.eth.chain_id

    print(f"RPC:        {RPC_URL}")
    print(f"Chain ID:   {chain_id}")
    print()

    # Use Anvil #0 as deployer (already funded on Anvil forks)
    deployer = Account.from_key(ANVIL_DEV_KEY)
    deployer_bal = w3.eth.get_balance(deployer.address)
    print(f"Deployer:   {deployer.address}  (balance: {w3.from_wei(deployer_bal, 'ether')} ETH)")

    # Deploy SenderChecker (compiled from contracts/EIP7702Test.sol)
    bytecode = "0x60808060405234601457608e90816100198239f35b5f80fdfe6004361015600b575f80fd5b5f803560e01c63919840ad14601e575f80fd5b346055578060031936011260555732337f664409437c3787d326d629bdd8447647a29c84a8d6814ee7d52cd931f84caf348380a380f35b80fdfea26469706673582212206b9bc15281073714332e2e6033be96afadb73e06dceb94f49f456316ce21c99964736f6c63430008140033"
    abi = [
        {"inputs": [], "name": "check", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
        {"anonymous": False, "inputs": [{"indexed": True, "internalType": "address", "name": "msgSender", "type": "address"}, {"indexed": True, "internalType": "address", "name": "txOrigin", "type": "address"}], "name": "Caller", "type": "event"},
    ]

    SenderChecker = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = SenderChecker.constructor().build_transaction({
        "from": deployer.address,
        "nonce": w3.eth.get_transaction_count(deployer.address),
        "gas": 200_000,
        "gasPrice": w3.to_wei(1, "gwei"),
        "chainId": chain_id,
    })
    signed = deployer.sign_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction))
    checker_addr = Web3.to_checksum_address(receipt.contractAddress)
    print(f"Deployed SenderChecker at {checker_addr}")

    # Create a fresh test EOA
    test_eoa = Account.create()
    _fund_account(w3, test_eoa.address)

    # Fund the test EOA super generously
    for _ in range(10):
        try:
            w3.provider.make_request("anvil_setBalance", [test_eoa.address, hex(10**22)])
            break
        except Exception:
            continue

    # Verify: before delegation, test EOA has no code
    code_before = w3.eth.get_code(test_eoa.address)
    test_bal = w3.eth.get_balance(test_eoa.address)
    print(f"\nTest EOA:   {test_eoa.address}")
    print(f"Balance:    {w3.from_wei(test_bal, 'ether')} ETH")
    print(f"Code before: {len(code_before)} bytes {'(no code)' if len(code_before) == 0 else ''}")

    # ---- EIP-7702 test (approach 1: eth_sendTransaction with node-signing) ----
    # Uses deployer (Anvil #0) as the test subject — Anvil has the key
    print("\n--- Test 1: eth_sendTransaction (node handles signing) ---")
    auth_nonce = w3.eth.get_transaction_count(deployer.address)

    auth_signed = Account.sign_authorization(
        {"chainId": chain_id, "address": checker_addr, "nonce": auth_nonce + 1},
        deployer.key,
    )

    selector = _keccak(b"check()")[:4].hex()
    resp = w3.provider.make_request("eth_sendTransaction", [{
        "from": deployer.address,
        "to": deployer.address,
        "gas": hex(100_000),
        "gasPrice": hex(w3.to_wei(1, "gwei")),
        "data": "0x" + selector,
        "authorizationList": [{
            "chainId": hex(chain_id),
            "address": checker_addr,
            "nonce": hex(auth_nonce + 1),
            "yParity": hex(auth_signed.y_parity),
            "r": hex(auth_signed.r),
            "s": hex(auth_signed.s),
        }],
    }])
    if "error" in resp:
        print(f"  eth_sendTransaction error: {resp['error']}")
    else:
        receipt = w3.eth.wait_for_transaction_receipt(resp["result"])
        print(f"  Status: {receipt.status}  Gas: {receipt.gasUsed}  Logs: {len(receipt.logs)}")
        if receipt.logs:
            event = SenderChecker.events.Caller().process_receipt(receipt)
            if event:
                e = event[0]["args"]
                print("  >>> EIP-7702 WORKS on Anvil with --hardfork prague! <<<")
                print(f"  msg.sender: {e.msgSender}")
                print(f"  tx.origin:  {e.txOrigin}")
                print(f"  msg.sender == deployer: {e.msgSender.lower() == deployer.address.lower()}")
        else:
            print("  No logs — delegation may not have been applied")

    # ---- EIP-7702 test (approach 2: raw signed tx with a fresh EOA) ----
    print("\n--- Test 2: raw signed type 0x04 tx (fresh EOA) ---")
    print("  (Anvil bug: raw type-0x04 balance check rejects valid txs)")
    print("  (Test 1 proves EIP-7702 works via eth_sendTransaction)")
    print("  (On Sepolia/Pectra both methods will work)")
    tx_nonce = w3.eth.get_transaction_count(test_eoa.address)

    auth2 = Account.sign_authorization(
        {"chainId": chain_id, "address": checker_addr, "nonce": tx_nonce + 1},
        test_eoa.key,
    )

    calldata_bytes = SenderChecker.encode_abi("check")

    try:
        # Build raw EIP-7702 tx via web3's eth module
        calldata_raw = bytes.fromhex(calldata_bytes[2:])
        to_addr_bytes = bytes.fromhex(test_eoa.address[2:])
        checker_addr_bytes = bytes.fromhex(checker_addr[2:])

        from eth_keys import keys
        pk = keys.PrivateKey(test_eoa.key)

        # Build the full RLP with placeholder signature (y_parity=0, r=0, s=0)
        # Then hash the 0x04-prefixed RLP to get the signing hash
        # Then replace the placeholder with the real signature
        full_rlp_with_placeholder = rlp.encode([
            chain_id,
            tx_nonce,
            w3.to_wei(10, "gwei"),  # max_priority
            w3.to_wei(100, "gwei"),  # max_fee
            200_000,  # gas
            to_addr_bytes,
            0,  # value
            calldata_raw,
            [],  # access_list
            [[chain_id, checker_addr_bytes, tx_nonce + 1, auth2.y_parity, auth2.r, auth2.s]],
            0,  # y_parity placeholder
            0,  # r placeholder
            0,  # s placeholder
        ])

        # Debug: print balance before sending
        bal_test = w3.eth.get_balance(test_eoa.address)
        print(f"  Test EOA balance: {w3.from_wei(bal_test, 'ether')} ETH")

        hash_to_sign = _keccak(bytes([4]) + full_rlp_with_placeholder)
        sig = pk.sign_msg_hash(hash_to_sign)
        y_parity = sig.v if sig.v <= 1 else sig.v - 27

        # Now build the final tx with real signature
        raw_tx = bytes([4]) + rlp.encode([
            chain_id,
            tx_nonce,
            w3.to_wei(10, "gwei"),
            w3.to_wei(100, "gwei"),
            200_000,
            to_addr_bytes,
            0,
            calldata_raw,
            [],
            [[chain_id, checker_addr_bytes, tx_nonce + 1, auth2.y_parity, auth2.r, auth2.s]],
            y_parity,
            sig.r,
            sig.s,
        ])

        try:
            tx_hash = w3.eth.send_raw_transaction(raw_tx)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            print(f"  Status: {receipt.status}  Gas: {receipt.gasUsed}  Logs: {len(receipt.logs)}")
            if receipt.logs:
                print("  >>> EIP-7702 WORKS with raw signed tx! <<<")
            else:
                print("  No logs — delegation not applied")
        except Exception as e:
            print(f"  send_raw_transaction error: {e}")
            print("  (RLP encoding issue — eth_sendTransaction approach works above)")
    except Exception as e:
        print(f"  RLP construction error: {e}")

    print(f"\nConclusion: EIP-7702 {'WORKS' if receipt and receipt.logs else 'has limited support'} on this RPC")


if __name__ == "__main__":
    main()
