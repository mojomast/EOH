"""P12: feed-down status (AIS timeout) and the reported adsb.lol flights fallback."""
from __future__ import annotations

import json

import pytest

from gev import composer, providers, watch


AREA = watch.normalize_area({"bbox": [45, -74, 46, -73]})


def _flight():
    return {"id": "icao:abc123", "lat": 45.5, "lon": -73.6, "title": "Flight TEST1",
            "description_md": "x", "links": []}


def test_flights_falls_back_to_adsb_lol_and_says_so(monkeypatch):
    calls = []

    def fake_get(url, timeout=20):
        calls.append(url)
        if "opensky-network.org" in url:
            return 503, ""
        if "adsb.lol" in url:
            return 200, json.dumps({"ac": [
                {"hex": "abc123", "flight": "TEST1 ", "lat": 45.5, "lon": -73.6,
                 "alt_baro": 30000, "gs": 400, "track": 90, "seen_pos": 0.5},
                {"hex": "def456", "flight": "GND1", "lat": 45.6, "lon": -73.5,
                 "alt_baro": "ground"},
            ]})
        return 500, ""

    monkeypatch.setattr(watch, "_get", fake_get)
    out = watch.feed_flights(AREA, {})
    assert out["status"] == "OK"
    assert out["fallback"] == "adsb.lol"
    assert "OpenSky" in out["note"] and "adsb.lol" in out["note"]
    assert [i["id"] for i in out["items"]] == ["icao:abc123"]
    assert any("adsb.lol" in url for url in calls)


def test_flights_fallback_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(watch, "_get", lambda url, timeout=20: (503, "") if "opensky" in url else (429, ""))
    out = watch.feed_flights(AREA, {})
    assert out["status"] == "ERROR"
    assert out["fallback"]["provider"] == "adsb.lol"
    assert out["fallback"]["ok"] is False
    assert out.get("fix")
    assert "OpenSky" in out["error"] and "adsb.lol" in out["error"]


def test_ais_timeout_is_feed_down_with_status_and_fix(monkeypatch):
    monkeypatch.setattr(watch, "key_value", lambda name: "SECRET-AIS-KEY" if name == "AISSTREAM_API_KEY" else None)

    def boom(*args, **kwargs):
        raise TimeoutError("AIS stream timed out")

    monkeypatch.setattr(composer, "mcp_call", boom)
    out = watch.feed_ships(AREA, {})
    assert out["status"] == "TIMEOUT"
    assert out["state"] == "timeout"
    assert out.get("fix") and "AIS" in out["fix"]
    assert "SECRET-AIS-KEY" not in json.dumps(out)


def test_watch_check_reports_fallback_and_feed_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSEYE_WATCH_LOOP", "0")

    def fetchers(feed):
        if feed == "flights":
            return lambda area, cond: {"status": "OK", "items": [_flight()], "fallback": "adsb.lol",
                                       "note": "OpenSky failed; flights came from adsb.lol."}
        return lambda area, cond: {"status": "TIMEOUT", "items": [], "state": "timeout",
                                   "error": "AIS stream timed out", "fix": "Retry AIS once."}

    st = watch.WatchStore(tmp_path / "w.json", fetchers={f: fetchers(f) for f in ("flights", "ships")},
                          clock=lambda: 1000.0)
    wid = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["flights", "ships"]})["watch"]["id"]
    report = st.check(wid)["feeds"]
    assert report["flights"]["fallback"] == "adsb.lol"
    assert "adsb.lol" in report["flights"]["note"]
    assert report["ships"]["status"] == "TIMEOUT"
    assert report["ships"]["fix"] == "Retry AIS once."


def test_probe_timeout_is_classified_and_carries_a_fix(monkeypatch):
    monkeypatch.setenv("AISSTREAM_API_KEY", "SECRET-AIS-KEY")
    def fetch(url, headers, timeout):
        if url.endswith("/api/vessels?limit=1"):
            raise TimeoutError("AIS stream timed out")
        return 200, b'{"ok":true}'

    rows = providers.probe_providers(fetch=fetch, file_env={"AISSTREAM_API_KEY": "SECRET-AIS-KEY"},
                                     api_base="http://127.0.0.1:9")
    ais = next(row for row in rows if row["provider"] == "ais")
    assert ais["status"] == "timeout"
    assert ais.get("fix") and "AIS" in ais["fix"]
    assert "SECRET-AIS-KEY" not in json.dumps(rows)
    assert providers.classify_probe("timeout", "") == "timeout"


def test_missing_key_probe_still_has_a_fix():
    rows = providers.probe_providers(fetch=lambda *a: (200, b"{}"), file_env={},
                                     api_base="http://127.0.0.1:9")
    missing = {row["provider"]: row for row in rows}
    assert missing["ais"]["status"] == "missing"
    assert missing["ais"].get("fix")
