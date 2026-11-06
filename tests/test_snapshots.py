"""Feature snapshot shape and the doctor's catalog_age check."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import composer  # noqa: E402

FEATURES = {
    "recent_imagery", "weather_stack", "transit", "fire_perimeters",
    "local_adsb", "director", "cyber_hud",
}


def _snapshot():
    return json.loads((ROOT / "gev" / "feature_snapshot.json").read_text())


def _generated_at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_feature_snapshot_shape():
    data = _snapshot()
    assert set(data) == {"generated_at", "gev_rev", "gev_version", "features"}
    assert _generated_at(data["generated_at"]).tzinfo is not None
    assert isinstance(data["gev_rev"], str) and data["gev_rev"]
    assert isinstance(data["gev_version"], str) and data["gev_version"]
    assert set(data["features"]) == FEATURES
    for name, feature in data["features"].items():
        assert set(feature) == {"present", "evidence"}, name
        assert isinstance(feature["present"], bool), name
        assert isinstance(feature["evidence"], list) and feature["evidence"], name
    assert data["features"]["recent_imagery"]["present"]
    assert data["features"]["transit"]["present"]
    assert data["features"]["fire_perimeters"]["present"]


def test_catalog_age_is_ok_before_thirty_days():
    data = _snapshot()
    now = _generated_at(data["generated_at"]) + timedelta(days=29)
    check = composer.catalog_age(now=now)
    assert check["check"] == "catalog_age"
    assert check["ok"] is True


def test_catalog_age_warns_at_thirty_days_and_suggests_the_script():
    data = _snapshot()
    now = _generated_at(data["generated_at"]) + timedelta(days=31)
    check = composer.catalog_age(now=now)
    assert check["check"] == "catalog_age"
    assert check["ok"] is False
    assert "scripts/snapshot_tools.sh" in check["fix"]


def test_catalog_age_warns_when_the_snapshot_is_missing(tmp_path):
    check = composer.catalog_age(path=tmp_path / "feature_snapshot.json")
    assert check["check"] == "catalog_age"
    assert check["ok"] is False
    assert "scripts/snapshot_tools.sh" in check["fix"]


def test_doctor_reports_catalog_age(monkeypatch):
    data = _snapshot()
    now = _generated_at(data["generated_at"]) + timedelta(days=1)
    monkeypatch.setattr(composer, "relay_status", lambda: {"paneOpen": False, "panes": []})
    monkeypatch.setattr(composer, "panel_checks", lambda **kw: [])
    monkeypatch.setattr(composer, "provider_key_checks", lambda *a, **k: [])
    monkeypatch.setattr(composer, "service_started_at", lambda: None)
    from gev import config
    monkeypatch.setattr(config, "node_bin", lambda: None)
    monkeypatch.setattr(config, "checkout", lambda: None)
    monkeypatch.setattr(config, "api_base", lambda: "http://127.0.0.1:9")
    doctor = composer.doctor(now=now)
    check = next(c for c in doctor["checks"] if c["check"] == "catalog_age")
    assert check["ok"] is True
