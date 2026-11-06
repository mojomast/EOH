"""Street-level keys come from Hermes get_secret only — never from env files."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from gev import streetlevel


def _install_get_secret(monkeypatch, fake):
    mod = types.ModuleType("agent.secret_scope")
    mod.get_secret = fake
    parent = types.ModuleType("agent")
    parent.secret_scope = mod
    monkeypatch.setitem(sys.modules, "agent", parent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", mod)


def test_uses_get_secret_not_env_file(tmp_path, monkeypatch):
    """A Gods Eye View env file must be ignored; get_secret is the only source."""
    env = tmp_path / "gods-eye-view.env"
    env.write_text('GOOGLE_MAPS_API_KEY="test-from-file-must-ignore"\n', encoding="utf-8")
    monkeypatch.setenv("GEV_ENV_FILE", str(env))
    monkeypatch.setenv("GODSEYE_ENV_FILE", str(env))
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("MAPILLARY_CLIENT_TOKEN", raising=False)

    calls: list[str] = []

    def fake_get_secret(name, default=None):
        calls.append(name)
        if name == "GOOGLE_MAPS_API_KEY":
            return "test-gmaps-from-hermes"
        return default

    _install_get_secret(monkeypatch, fake_get_secret)

    urls = []

    def fake_get(url, headers=None, timeout=8):
        urls.append(url)
        assert "test-gmaps-from-hermes" in url
        assert "test-from-file-must-ignore" not in url
        return 0, ""

    monkeypatch.setattr(streetlevel, "_get", fake_get)
    out = streetlevel.nearest(45.5, -73.57)
    assert calls and "GOOGLE_MAPS_API_KEY" in calls
    assert urls and "test-gmaps-from-hermes" in urls[0]
    blob = json.dumps(out)
    assert "test-from-file-must-ignore" not in blob
    assert "test-gmaps-from-hermes" not in blob


def test_missing_keys_return_key_missing_with_hermes_fix(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("MAPILLARY_CLIENT_TOKEN", raising=False)

    _install_get_secret(monkeypatch, lambda name, default=None: default)

    out = streetlevel.nearest(45.5, -73.57)
    assert out["ok"] is False
    assert out["error"] == "KEY_MISSING"
    assert "GOOGLE_MAPS_API_KEY" in out["missingKeys"]
    assert "MAPILLARY_CLIENT_TOKEN" in out["missingKeys"]
    assert "Hermes" in out["fix"]
    assert "gods-eye-view.env" not in out["fix"]
    assert "GEV_ENV_FILE" not in out["fix"]


def test_no_path_helpers_remain():
    assert not hasattr(streetlevel, "_gev_env_paths")
    assert not hasattr(streetlevel, "_read_env_file")
    assert not hasattr(streetlevel, "_secret_from_env_files")
    src = Path(streetlevel.__file__).read_text(encoding="utf-8")
    assert "gods-eye-view.env" not in src
    assert "GEV_ENV_FILE" not in src
    assert "GODSEYE_ENV_FILE" not in src
    assert "os.environ.get" not in src
    assert "from os" not in src and "import os" not in src
    assert "get_secret" in src


def test_google_api_key_alternate_via_get_secret(monkeypatch):
    calls = []

    def fake_get_secret(name, default=None):
        calls.append(name)
        if name == "GOOGLE_API_KEY":
            return "test-google-api-alt"
        return None

    _install_get_secret(monkeypatch, fake_get_secret)
    urls = []
    monkeypatch.setattr(
        streetlevel, "_get",
        lambda url, headers=None, timeout=8: urls.append(url) or (0, ""),
    )
    streetlevel.nearest(45.5, -73.57)
    assert "GOOGLE_MAPS_API_KEY" in calls and "GOOGLE_API_KEY" in calls
    assert urls and "test-google-api-alt" in urls[0]
