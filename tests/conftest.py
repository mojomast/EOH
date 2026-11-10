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


@pytest.fixture(autouse=True)
def _hermes_secret_scope_shim(monkeypatch):
    """When Hermes is not on PYTHONPATH, stub agent.secret_scope.get_secret.

    Production code calls get_secret only (catalog rule 11). Outside Hermes the
    shim mirrors process env so existing cassette tests keep working; it never
    reads an env file.
    """
    import sys
    import types

    try:
        from agent.secret_scope import get_secret as _real  # noqa: F401
        return
    except ImportError:
        pass

    def get_secret(name, default=None):
        return os.environ.get(name, default)

    parent = sys.modules.get("agent")
    if parent is None:
        parent = types.ModuleType("agent")
        monkeypatch.setitem(sys.modules, "agent", parent)
    mod = types.ModuleType("agent.secret_scope")
    mod.get_secret = get_secret
    parent.secret_scope = mod
    monkeypatch.setitem(sys.modules, "agent.secret_scope", mod)


def pytest_configure(config):
    """If Hermes is not on PYTHONPATH, provide a minimal hermes_constants stub.

    Production code calls hermes_constants.get_hermes_home() (never a raw
    ~/.hermes path). CI and contract runs should set PYTHONPATH to a Hermes
    checkout so the real module is used. This stub only fills the import gap
    for a contributor who has installed the dev extras and is running pytest.
    It honors HERMES_HOME and otherwise uses Path.home() / ".hermes".
    """
    try:
        import hermes_constants  # noqa: F401
        return
    except ImportError:
        pass

    import sys
    import types
    from pathlib import Path

    def get_hermes_home():
        raw = os.environ.get("HERMES_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path.home() / ".hermes"

    mod = types.ModuleType("hermes_constants")
    mod.get_hermes_home = get_hermes_home
    sys.modules["hermes_constants"] = mod
