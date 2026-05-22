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

from eth_account import Account
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

    # Deploy SenderChecker
    # Solidity:
    #   contract SenderChecker {
    #       event Caller(address indexed caller, address indexed origin);
    #       function check() external {
    #           emit Caller(msg.sender, tx.origin);
    #       }
    #   }
    bytecode = "6080604052348015600e575f80fd5b5060df8061001b5f395ff3fe6080604052348015600e575f80fd5b50600436106026575f3560e01c8063a87a20bc14602a575b5f80fd5b60306032565b005b7f9c2b3401b9a3ae678e5f7d1ae0f91bd2ba21c3f5cbe66706e9a6c2895fb7a6133604080513381523242602082015281519081900390910190a256fea264697066735822122019b32d5cc0b88e304d74ffbbae4b8f588d2b86a29bb1ec14e9e0be62609cd0c964736f6c63430204004233"
    abi = [
        {"inputs": [], "name": "check", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
        {"anonymous": False, "inputs": [{"indexed": True, "internalType": "address", "name": "caller", "type": "address"}, {"indexed": True, "internalType": "address", "name": "origin", "type": "address"}], "name": "Caller", "type": "event"},
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

    # Verify: before delegation, test EOA has no code
    code_before = w3.eth.get_code(test_eoa.address)
    print(f"\nTest EOA:   {test_eoa.address}")
    print(f"Code before: {len(code_before)} bytes {'(no code)' if len(code_before) == 0 else ''}")

    # ---- EIP-7702 test ----
    # 1. Sign authorization delegating to SenderChecker
    auth_nonce = w3.eth.get_transaction_count(test_eoa.address)
    authorization = Account.sign_authorization(
        {
            "chainId": chain_id,
            "address": checker_addr,
            "nonce": auth_nonce,
        },
        test_eoa.key,
    )

    # 2. Encode check() calldata
    calldata = SenderChecker.encode_abi("check")

    # 3. Send self-call tx with authorizationList
    tx = {
        "chainId": chain_id,
        "from": test_eoa.address,
        "to": test_eoa.address,
        "nonce": auth_nonce,
        "value": 0,
        "gasPrice": w3.to_wei(1, "gwei"),
        "gas": 100_000,
        "data": calldata,
        "authorizationList": [authorization],
    }
    try:
        estimated = w3.eth.estimate_gas(tx)
        tx["gas"] = estimated + 10_000
        signed_tx = test_eoa.sign_transaction(tx)
        receipt = w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        )
        print(f"\nSelf-call tx: {receipt.transactionHash.hex()[:20]}...")
        print(f"Status:       {receipt.status}")
        print(f"Gas used:     {receipt.gasUsed}")
        print(f"Logs:         {len(receipt.logs)}")

        # 4. Check code after tx
        code_after = w3.eth.get_code(test_eoa.address)
        print(f"Code after:  {len(code_after)} bytes {'(code set!)' if len(code_after) > 0 else '(still EOA — EIP-7702 NOT supported)'}")

        # 5. Parse event
        if receipt.logs:
            event = SenderChecker.events.Caller().process_receipt(receipt)
            if event:
                e = event[0]["args"]
                print(f"\n>>> EIP-7702 WORKING <<<")
                print(f"  msg.sender: {e.caller}")
                print(f"  tx.origin:  {e.origin}")
                print(f"  msg.sender is test EOA: {e.caller.lower() == test_eoa.address.lower()}")
            else:
                print("\nEvent present but not parsed. Raw event log:")
                for log in receipt.logs:
                    print(f"  address={log.address}")
                    print(f"  topics={[t.hex() for t in log.topics]}")
        else:
            print("\n>>> EIP-7702 NOT supported <<<")
            print("No events emitted — self-call to EOA had no effect")

    except Exception as exc:
        print(f"\nError during self-call: {exc}")
        print(f"\n>>> EIP-7702 NOT supported <<<")

    # Verify code persistence (EIP-7702 code lasts only for the tx duration)
    code_final = w3.eth.get_code(test_eoa.address)
    print(f"Code final:  {len(code_final)} bytes {'(ephemeral — per-tx only)' if len(code_final) == 0 and len(code_before) == 0 else ''}")


if __name__ == "__main__":
    main()
