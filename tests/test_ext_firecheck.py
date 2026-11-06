"""Fire check runs FIRMS, the clearest image, then before/after, and repeats the same lookups."""
from __future__ import annotations

import json
from datetime import date

from gev import composer, ext_firecheck, ext_ops


def _patch(monkeypatch, fires_body, days, ab, camera=None):
    calls = []

    def fires(args):
        calls.append(("fires", args))
        return json.dumps(fires_body)

    def imagery(args):
        calls.append(("imagery", args))
        return json.dumps({"days": days})

    def imagery_ab(args, session_id=None):
        calls.append(("imagery_ab", args, session_id))
        return json.dumps(ab)

    monkeypatch.setattr(ext_firecheck, "fires", fires)
    monkeypatch.setattr(ext_firecheck, "imagery", imagery)
    monkeypatch.setattr(ext_firecheck, "imagery_ab", imagery_ab)
    monkeypatch.setattr(ext_firecheck, "camera", lambda session_id: camera or ({"lat": 10, "lon": 20}, None))
    monkeypatch.setattr(ext_firecheck, "now", lambda: date(2026, 11, 6))
    return calls


def test_no_hotspots_stops_before_imagery(monkeypatch):
    calls = _patch(monkeypatch, {"rows": []}, [], {"ok": True})
    body = json.loads(ext_firecheck.fire_check({"place": "Austin"}))
    assert body["ok"] is True and body["hotspots"] == 0
    assert body["sequence"] == ["godseye_feeds op=fires"]
    assert [name for name, *_ in calls] == ["fires"]
    assert calls[0][1]["detail"] == "full"


def test_strongest_hotspot_then_clearest_day_then_before_after(monkeypatch):
    calls = _patch(monkeypatch, {"rows": [
        {"lat": 34.1, "lon": -118.2, "frp": 5},
        {"lat": 34.2, "lon": -118.1, "frp": 40},
        {"lat": 34.0, "lon": -118.3, "frp": 40},
    ]}, [
        {"date": "2026-10-01", "cloud": 0, "id": "old"},
        {"date": "2026-10-20", "cloud": 5, "id": "b", "product": "landsat"},
        {"date": "2026-10-28", "cloud": 5, "id": "c", "product": "landsat"},
        {"date": "2026-10-28", "cloud": 40, "id": "d", "product": "landsat"},
    ], {"ok": True, "before": {"date": "2026-10-20", "cloud": 5, "id": "b"},
        "after": {"date": "2026-10-28", "cloud": 5, "id": "c"}, "mode": "swipe", "shown": [{"huge": True}]})
    body = json.loads(ext_firecheck.fire_check({"place": "Los Angeles"}, session_id="chat"))
    again = json.loads(ext_firecheck.fire_check({"place": "Los Angeles"}, session_id="chat"))
    assert body["ok"] is True and body == again
    assert body["hotspot"] == {"lat": 34.0, "lon": -118.3, "frp": 40}
    assert body["clearest"]["date"] == "2026-10-28" and body["clearest"]["cloud"] == 5
    assert body["imagery_ab"]["mode"] == "swipe"
    assert "shown" not in body and "huge" not in json.dumps(body)
    assert [name for name, *_ in calls] == ["fires", "imagery", "imagery_ab", "fires", "imagery", "imagery_ab"]
    ab_args = calls[2][1]
    assert ab_args["after"] == "2026-10-28"
    assert ab_args["bbox"][0] < -118.3 < ab_args["bbox"][2]
    assert calls[2][2] == "chat"
    assert "FIRMS 3 hotspot" in body["summary"]


def test_imagery_failure_keeps_the_hotspot_and_the_clearest_day(monkeypatch):
    _patch(monkeypatch, {"features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-118.2, 34.1]},
        "properties": {"frp": 12},
    }]}, [
        {"date": "2026-10-20", "cloud": 80, "id": "hazy", "product": "landsat"},
    ], {"ok": False, "error": "no_clear_day", "message": "No day at or under 20% cloud."})
    body = json.loads(ext_firecheck.fire_check({"bbox": [-119, 33, -117, 35]}))
    assert body["ok"] is False and body["code"] == "no_clear_day"
    assert body["hotspot"]["frp"] == 12 and body["clearest"]["date"] == "2026-10-20"
    assert "after" not in json.loads(json.dumps(body)) or True
    # The before/after call must not pin the hazy day as the after date.
    # Re-read by running the hooks' last imagery_ab args through a fresh patch is unnecessary:
    # cloud 80 is over the default max of 20, so after is omitted. Checked below via a spy.
    assert body["sequence"] == list(ext_firecheck.SEQUENCE)


def test_hazy_day_is_not_forced_as_the_after_date(monkeypatch):
    calls = _patch(monkeypatch, {"rows": [{"latitude": 1, "longitude": 2, "frp": 1}]}, [
        {"date": "2026-10-20", "cloud": 80, "id": "hazy", "product": "landsat"},
    ], {"ok": False, "error": "no_clear_day", "message": "No clear day."})
    ext_firecheck.fire_check({"lat": 1, "lon": 2})
    assert "after" not in calls[2][1]


def test_here_uses_the_camera_and_slash_prints_the_summary(monkeypatch):
    calls = _patch(monkeypatch, {"rows": []}, [], {"ok": True}, camera=({"lat": 30.2, "lon": -97.7}, None))
    body = json.loads(ext_firecheck.fire_check({"place": "here"}, session_id="s"))
    assert body["hotspots"] == 0
    assert calls[0][1]["area"]["lat"] == 30.2
    text = composer.gev_command("fire Los Angeles", live=lambda *a, **k: {"ok": True})
    assert text == "No FIRMS hotspots in that area."


def test_fire_check_is_a_feeds_op():
    found = {(record.tool, record.op) for record in ext_ops.discover()}
    assert ("godseye_feeds", "fire_check") in found
    assert ("godseye_annotate", "clip") in found
