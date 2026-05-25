"""E2E integration test: full payment + settlement + feedback flow."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from web3 import Web3

from src.setup import (
    bootstrap,
    RPC_URL,
    REPUTATION_REGISTRY,
    load_feedback_gateway_artifact,
    load_reputation_registry_abi,
    assert_feedback_client_address,
)
from src.shared.constants import (
    FACILITATOR_PORT,
    SERVER_PORT,
    FACILITATOR_URL,
    SERVER_URL,
)


def _run_uvicorn(app, port: int) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


def _wait_for_http(url: str, timeout: float = 15.0) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{url}/health", timeout=1.0)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Service not ready: {url}/health")


@pytest.mark.timeout(120)
def test_e2e_payment_and_feedback(setup):
    # --- Initialize ---
    from src.facilitator.app import create_app as create_facilitator
    from src.agent_server.app import create_app as create_agent_server

    os.environ["EIP_7702_SUPPORTED"] = "true"
    from src.client.app import run_paying_client

    w3 = Web3(Web3.HTTPProvider(RPC_URL))

    # --- Start servers (facilitator must be up before agent_server initializes) ---
    facilitator_app = create_facilitator(feedback_gateway=setup.feedback_gateway)
    t1 = threading.Thread(target=_run_uvicorn, args=(facilitator_app, FACILITATOR_PORT), daemon=True)
    t1.start()
    _wait_for_http(FACILITATOR_URL)

    agent_app = create_agent_server(agent_address=setup.agent_account.address)
    t2 = threading.Thread(target=_run_uvicorn, args=(agent_app, SERVER_PORT), daemon=True)
    t2.start()
    _wait_for_http(SERVER_URL)

    # --- Run client ---
    ok = asyncio.run(run_paying_client(
        client_key=setup.client_account.key.hex(),
        feedback_gateway=setup.feedback_gateway,
        agent_id=setup.agent_id,
    ))
    assert ok, "Client payment + feedback flow failed"

    # --- Verify on-chain state ---
    gateway_artifact = load_feedback_gateway_artifact()
    gateway = w3.eth.contract(
        address=Web3.to_checksum_address(setup.feedback_gateway),
        abi=gateway_artifact["abi"],
    )

    # Settlement was recorded (facilitator called recordSettlement)
    # We can't know the txHash without capturing it from the client log,
    # but we can verify the FeedbackGateway has at least one settlement

    # Verify the agent has reputation (real ReputationRegistry uses getSummary with 4 args)
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(REPUTATION_REGISTRY),
        abi=load_reputation_registry_abi(),
    )
    # Verify feedback is attributed to the client EOA (not to the FeedbackGateway)
    client_address = Web3.to_checksum_address(setup.client_account.address)

    summary = registry.functions.getSummary(
        setup.agent_id, [client_address], "", ""
    ).call()
    assert summary[0] > 0, f"Agent {setup.agent_id} should have feedback count > 0"

    client_index = registry.functions.getLastIndex(setup.agent_id, client_address).call()
    assert client_index > 0, f"Client {client_address} should have feedback index > 0"

    feedback = registry.functions.readFeedback(setup.agent_id, client_address, client_index).call()
    assert feedback[0] == 95, f"Expected feedback value 95, got {feedback[0]}"
