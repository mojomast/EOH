"""Street-level keys fall back to ~/.config/gods-eye-view.env (never leak values)."""
from __future__ import annotations

import json
from pathlib import Path

from gev import streetlevel


def test_google_key_from_gev_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEV_ENV_FILE", raising=False)
    monkeypatch.delenv("GODSEYE_ENV_FILE", raising=False)
    env = tmp_path / "gods-eye-view.env"
    env.write_text('GOOGLE_MAPS_API_KEY="test-gmaps-from-file"\n', encoding="utf-8")
    monkeypatch.setenv("GEV_ENV_FILE", str(env))
    called = []

    def fake_get(url, headers=None, timeout=8):
        called.append(url)
        assert "test-gmaps-from-file" in url
        return 0, ""

    monkeypatch.setattr(streetlevel, "_get", fake_get)
    out = streetlevel.nearest(45.5, -73.57)
    assert called and out["error"] == "no_pano"
    blob = json.dumps(out)
    assert "test-gmaps-from-file" not in blob
    assert "GOOGLE_MAPS_API_KEY" in (out.get("missingKeys") or []) or out.get("error") == "no_pano"


def test_mapillary_token_from_godseye_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("MAPILLARY_CLIENT_TOKEN", raising=False)
    env = tmp_path / "gev.env"
    env.write_text("MAPILLARY_CLIENT_TOKEN=test-mapillary-from-file\n", encoding="utf-8")
    monkeypatch.setenv("GODSEYE_ENV_FILE", str(env))
    seen = []

    def fake_get(url, headers=None, timeout=8):
        seen.append((url, headers))
        return 0, ""

    monkeypatch.setattr(streetlevel, "_get", fake_get)
    out = streetlevel.nearest(45.5, -73.57)
    assert any((h or {}).get("Authorization") == "OAuth test-mapillary-from-file" for _, h in seen)
    assert "test-mapillary-from-file" not in json.dumps(out)


def test_process_env_wins_over_file(tmp_path, monkeypatch):
    env = tmp_path / "gev.env"
    env.write_text("GOOGLE_MAPS_API_KEY=test-from-file\n", encoding="utf-8")
    monkeypatch.setenv("GEV_ENV_FILE", str(env))
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-from-process")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("MAPILLARY_CLIENT_TOKEN", raising=False)
    urls = []
    monkeypatch.setattr(streetlevel, "_get", lambda url, headers=None, timeout=8: urls.append(url) or (0, ""))
    streetlevel.nearest(45.5, -73.57)
    assert urls and "test-from-process" in urls[0]
    assert "test-from-file" not in urls[0]


def test_helpers_never_echo_values(tmp_path, monkeypatch):
    env = tmp_path / "gev.env"
    env.write_text("GOOGLE_MAPS_API_KEY=test-gmaps-xyz\n", encoding="utf-8")
    monkeypatch.setenv("GEV_ENV_FILE", str(env))
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    key = streetlevel._google_key()
    assert key == "test-gmaps-xyz"
    # Public helpers that surface to tools must not include the raw key in missingKeys messages alone —
    # nearest() already redacts; ensure _clean strips query tokens.
    assert "test-gmaps-xyz" not in streetlevel._clean(
        "https://example/?key=test-gmaps-xyz&x=1")
