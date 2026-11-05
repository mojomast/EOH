"""The suite does not call public demo servers.

Loopback is allowed (the relay and bridge tests start a local HTTP server).
Everything else raises NetworkBlocked unless a test plays a cassette from
tests/fixtures/cassettes. Set GODSEYE_E2E=1 only for the opt-in live test.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _block_public_network(monkeypatch):
    if os.environ.get("GODSEYE_E2E") == "1":
        yield
        return
    from netblock import deactivate, install
    install(monkeypatch)
    yield
    deactivate()
