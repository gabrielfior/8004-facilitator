"""Facilitator: x402 verify/settle + recordSettlement on-chain after each settlement."""

from __future__ import annotations

import json
import logging
import os

from eth_account import Account
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from web3 import Web3

from x402 import x402Facilitator
from x402.schemas import PaymentPayload, PaymentRequirements
from x402.mechanisms.evm import FacilitatorWeb3Signer
from x402.mechanisms.evm.exact import register_exact_evm_facilitator

from src.shared.constants import (
    RPC_URL,
    NETWORK,
    DEFAULT_FACILITATOR_KEY,
    ROOT,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("facilitator")


class FacilitatorPaymentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    x402_version: int = Field(default=2, alias="x402Version")
    payment_payload: PaymentPayload = Field(alias="paymentPayload")
    payment_requirements: PaymentRequirements = Field(alias="paymentRequirements")


def load_feedback_gateway_artifact() -> dict:
    artifact_path = ROOT / "out" / "FeedbackGateway.sol" / "FeedbackGateway.json"
    if not artifact_path.exists():
        raise RuntimeError(f"Missing {artifact_path}. Run: cd {ROOT} && forge build")
    return json.loads(artifact_path.read_text())


def create_app(
    feedback_gateway: str,
    facilitator_key: str = DEFAULT_FACILITATOR_KEY,
) -> FastAPI:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    facilitator_account = Account.from_key(facilitator_key)
    gateway_artifact = load_feedback_gateway_artifact()
    gateway = w3.eth.contract(
        address=Web3.to_checksum_address(feedback_gateway),
        abi=gateway_artifact["abi"],
    )

    evm_signer = FacilitatorWeb3Signer(
        private_key=facilitator_key,
        rpc_url=RPC_URL,
    )
    core = x402Facilitator()
    register_exact_evm_facilitator(core, evm_signer, networks=NETWORK)

    app = FastAPI(title="x402 Facilitator", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/verify")
    async def verify(request: FacilitatorPaymentRequest) -> dict:
        try:
            response = await core.verify(request.payment_payload, request.payment_requirements)
            return response.model_dump(by_alias=True, exclude_none=True)
        except Exception as exc:
            logger.exception("verify failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/settle")
    async def settle(request: FacilitatorPaymentRequest) -> dict:
        try:
            response = await core.settle(request.payment_payload, request.payment_requirements)
        except Exception as exc:
            logger.exception("settle failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        result = response.model_dump(by_alias=True, exclude_none=True)

        if response.success and response.transaction and response.payer:
            tx_hash = Web3.to_bytes(hexstr=response.transaction)
            payer = Web3.to_checksum_address(response.payer)
            try:
                gateway.functions.recordSettlement(tx_hash, payer).transact({
                    "from": facilitator_account.address,
                })
                logger.info("recordSettlement(txHash=%s..., payer=%s)", tx_hash.hex()[:16], payer)
            except Exception as exc:
                logger.warning("recordSettlement failed (non-fatal): %s", exc)

        return result

    @app.get("/supported")
    async def supported() -> dict:
        response = core.get_supported()
        return {
            "kinds": [k.model_dump(by_alias=True, exclude_none=True) for k in response.kinds],
            "extensions": response.extensions,
            "signers": response.signers,
        }

    @app.get("/gateway")
    async def gateway_addr() -> dict:
        return {"feedbackGateway": feedback_gateway}

    return app
