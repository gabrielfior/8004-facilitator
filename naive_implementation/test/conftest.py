"""Pytest fixture: starts Anvil, runs setup, tears down after tests."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure naive_implementation/ is on sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pytest

from src.setup import bootstrap, RPC_URL

ANVIL_BIN = os.getenv("ANVIL_BIN", "anvil")


@pytest.fixture(scope="session")
def anvil():
    proc = subprocess.Popen(
        [ANVIL_BIN, "--fork-url", RPC_URL, "--chain-id", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(3)
    try:
        yield
    finally:
        proc.terminate()
        proc.wait()


@pytest.fixture(scope="session")
def setup(anvil):
    return bootstrap()
