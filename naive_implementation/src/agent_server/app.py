"""Resource server: x402-paid /weather endpoint. No reputation logic."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from x402 import x402ResourceServer
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.types import RouteConfig
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import AssetAmount

from src.shared.constants import (
    FACILITATOR_URL,
    NETWORK,
    MAINNET_USDC_ADDRESS,
    DAI_ADDRESS,
    PRICE_AMOUNT,
    PAYMENT_TOKEN,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("agent_server")


def create_app(agent_address: str) -> FastAPI:
    facilitator_client = HTTPFacilitatorClient(FacilitatorConfig(url=FACILITATOR_URL))
    resource_server = x402ResourceServer(facilitator_client)
    resource_server.register(NETWORK, ExactEvmServerScheme())
    resource_server.initialize()

    dai_price = str(int(int(PRICE_AMOUNT) * (10**18) / (10**6)))

    usdc_option = PaymentOption(
        scheme="exact",
        pay_to=agent_address,
        price=AssetAmount(
            amount=PRICE_AMOUNT,
            asset=MAINNET_USDC_ADDRESS,
            extra={"name": "USD Coin", "version": "2"},
        ),
        network=NETWORK,
    )
    dai_option = PaymentOption(
        scheme="exact",
        pay_to=agent_address,
        price=AssetAmount(
            amount=dai_price,
            asset=DAI_ADDRESS,
            extra={"name": "Dai Stablecoin", "version": "1", "assetTransferMethod": "permit2"},
        ),
        network=NETWORK,
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

    app = FastAPI(title="x402 Resource Server", version="0.1.0")
    app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=resource_server)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/weather")
    async def weather() -> dict:
        return {"report": {"weather": "sunny", "temperature": 72}}

    return app
