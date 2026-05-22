from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")

# x402 ports
FACILITATOR_PORT = int(os.getenv("FACILITATOR_PORT", "4022"))
SERVER_PORT = int(os.getenv("SERVER_PORT", "4021"))
FACILITATOR_URL = os.getenv("FACILITATOR_URL", f"http://127.0.0.1:{FACILITATOR_PORT}")
SERVER_URL = os.getenv("SERVER_URL", f"http://127.0.0.1:{SERVER_PORT}")

# Mainnet token addresses
MAINNET_USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
DAI_ADDRESS = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

# ERC-8004 contract addresses
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REPUTATION_REGISTRY = "0x8004BAa17C55a88189AE136b182e5fdA19dE9b63"

# Anvil dev accounts (#0 has ETH + USDC on fork)
DEFAULT_FACILITATOR_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_CLIENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

PRICE_AMOUNT = "10000"  # $0.01 USDC (6 decimals)
FUND_USDC = 50_000_000  # 50 USDC
FUND_DAI = 100 * 10**18  # 100 DAI

PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN", "usdc").lower()  # "usdc" or "dai"

ERC8004_CONTRACTS_ROOT = ROOT / "lib" / "erc-8004-contracts"

from x402.schemas import Network
NETWORK: Network = "eip155:1"
