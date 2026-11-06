"""godseye_query op=osm falls back to public Overpass mirrors when pane is unconfigured."""
from __future__ import annotations

import json

from gev import poi


def test_osm_query_fallback_uses_find_places_mirrors(monkeypatch):
    seen = {}

    def fake_find(args):
        seen["args"] = args
        return {"ok": True, "count": 2, "places": [{"name": "A"}, {"name": "B"}], "source": "overpass:overpass-api.de"}

    monkeypatch.setattr(poi, "find_places", fake_find)
    out = poi.osm_query_fallback(
        {"what": "hospitals", "limit": 10},
        {"ok": False, "code": "OVERPASS_NOT_CONFIGURED", "unavailable": True},
        view={"camera": {"bbox": [45.4, -73.7, 45.6, -73.4]}},
    )
    assert out["ok"] is True and out["fallback"] == "public_overpass"
    assert out["count"] == 2 and "public mirrors" in out["degraded"]
    assert seen["args"]["category"] == "hospitals"
    assert seen["args"]["bbox"] == [45.4, -73.7, 45.6, -73.4]


def test_osm_query_fallback_noop_when_not_unconfigured():
    original = {"ok": False, "code": "AREA_TOO_LARGE"}
    assert poi.osm_query_fallback({"what": "cafes"}, original, view={"camera": {"lat": 1, "lon": 2}}) is original


def test_osm_query_fallback_needs_area_without_view(monkeypatch):
    monkeypatch.setattr(poi, "find_places", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not call")))
    out = poi.osm_query_fallback(
        {"what": "cafes"},
        {"ok": False, "error": "OVERPASS_NOT_CONFIGURED"},
        view=None,
    )
    assert out.get("ok") is not True
    assert "OVERPASS_UPSTREAMS" in (out.get("remediation") or out.get("fix") or "")


def test_live_handler_wires_fallback(monkeypatch):
    from gev import hermes_tools

    calls = []

    def live_call(name, args, session_id=None, fanout=False):
        calls.append(name)
        if name == "osm_query":
            return {"ok": False, "code": "OVERPASS_NOT_CONFIGURED", "unavailable": True}
        if name == "get_current_view_state":
            return {"camera": {"latitude": 45.5, "longitude": -73.57}}
        return {"ok": False}

    monkeypatch.setattr("gev.composer.live_call", live_call)
    monkeypatch.setattr(poi, "find_places", lambda args: {
        "ok": True, "count": 1, "places": [{"name": "Cafe"}], "source": "overpass:kumi",
        "bbox": args.get("bbox"),
    })
    handler = hermes_tools.make_live_handler("osm_query")
    text = handler({"what": "cafes", "limit": 5}, session_id="s1")
    data = json.loads(text)
    assert data["ok"] is True and data["fallback"] == "public_overpass"
    assert calls[0] == "osm_query" and "get_current_view_state" in calls
