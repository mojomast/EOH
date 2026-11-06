"""WFIGS fire perimeters: watch growth alerts, drawers, and timeline events."""
from __future__ import annotations

import calendar
import copy
import json
from pathlib import Path

import pytest

from gev import ext_fire, ext_ops, timeline, watch

FIXTURE = json.loads((Path(__file__).with_name("fixtures") / "wfigs_perimeters.json").read_text())
AREA = [44.5, -122.0, 46.0, -120.5]
NOW = calendar.timegm((2025, 7, 6, 0, 0, 0))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeMcp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return {"structuredContent": copy.deepcopy(self.payload)}


@pytest.fixture(autouse=True)
def no_loop(monkeypatch):
    monkeypatch.setenv("GODSEYE_WATCH_LOOP", "0")


def make_store(tmp_path):
    return watch.WatchStore(tmp_path / "w.json", clock=Clock())


def test_fire_perimeter_watch_alerts_on_new_and_grown_only(tmp_path, monkeypatch):
    payload = copy.deepcopy(FIXTURE)
    fake = FakeMcp(payload)
    monkeypatch.setattr(ext_fire, "mcp", fake)
    st = make_store(tmp_path)

    created = st.create({
        "area": {"bbox": AREA},
        "feeds": ["fire_perimeters"],
        "conditions": {"fire_perimeters": {"grow_pct": 10}},
    })
    assert created["ok"], created
    wid = created["watch"]["id"]

    first = st.check(wid)
    assert first["baseline_run"] is True and first["new"] == 0
    assert first["feeds"]["fire_perimeters"]["baselined"] == 3
    assert fake.calls[0][0] == "get_fire_perimeters"

    unchanged = st.check(wid)
    assert unchanged["new"] == 0 and not st.history()

    payload["perimeters"][0]["GISAcres"] = 1320
    payload["perimeters"].append({
        "UniqueFireIdentifier": "2025-CA-CDF-000999",
        "IncidentName": "New Start",
        "GISAcres": 40,
        "PercentContained": 0,
        "FireDiscoveryDateTime": "2025-07-06T01:00:00Z",
        "ModifiedDate": "2025-07-06T01:00:00Z",
        "lat": 45.2,
        "lon": -121.1,
    })
    changed = st.check(wid)
    assert changed["new"] == 2 and changed["feeds"]["fire_perimeters"]["new"] == 2
    titles = [item["title"] for item in st.history()[-1]["items"]]
    assert any("Cedar Creek" in title for title in titles)
    assert any("New Start" in title for title in titles)
    assert not any("Ridge" in title for title in titles)

    again = st.check(wid)
    assert again["new"] == 0

    payload["perimeters"][0]["GISAcres"] = 1400
    assert st.check(wid)["new"] == 0

    payload["perimeters"][0]["GISAcres"] = 1650
    grown = st.check(wid)
    assert grown["new"] == 1
    assert "Cedar Creek" in st.history()[-1]["items"][0]["title"]


def test_perimeter_drawer_fields_and_only_real_urls():
    annotation = ext_fire.perimeter_drawer(FIXTURE["perimeters"][0])
    assert annotation["type"] == "pin"
    assert annotation["title"] == "Cedar Creek"
    assert annotation["label"] == "Cedar Creek"
    assert annotation["latitude"] == 45.12 and annotation["longitude"] == -121.34
    body = annotation["description_md"]
    assert "Cedar Creek" in body and "1,200" in body and "35%" in body
    assert "Lightning" in body and "412" in body
    assert annotation["links"] == [{
        "label": "InciWeb",
        "url": "https://inciweb.wildfire.gov/incident-information/ca-cdf-000123",
    }]

    plain = ext_fire.perimeter_drawer(FIXTURE["perimeters"][1])
    assert plain["links"] == []
    assert "http" not in plain["description_md"]
    assert "340" in plain["description_md"] and "80%" in plain["description_md"]


def test_timeline_lists_perimeter_discovery_and_update_events(monkeypatch):
    monkeypatch.setattr(ext_fire, "mcp", FakeMcp(FIXTURE))
    area = watch.normalize_area({"bbox": AREA})
    frame = timeline.event_frame(
        {"feeds": ["fire_perimeters"], "from": "2025-07-01", "to": "2025-07-05"},
        area=area, session_id="s", watches=None, clock=lambda: NOW)
    assert frame["ok"] is True
    assert "fire_perimeters" in timeline.FEEDS
    times = [event["time"] for event in frame["events"]]
    assert times == sorted(times)
    assert [event["title"] for event in frame["events"]] == [
        "Cedar Creek (discovered)",
        "Ridge (discovered)",
        "Ridge (updated)",
        "Cedar Creek (updated)",
        "Pine Hollow (updated)",
    ]
    assert {event["feed"] for event in frame["events"]} == {"fire_perimeters"}
    assert all(event["color"] == "red" for event in frame["events"])


def test_watch_and_annotate_ops_are_registered(tmp_path, monkeypatch):
    monkeypatch.setattr(ext_fire, "mcp", FakeMcp(FIXTURE))
    st = make_store(tmp_path)
    monkeypatch.setattr(watch, "_store", st)

    watch_op = ext_ops.find("godseye_watch", "fire_perimeters")
    assert watch_op is not None and watch_op.props["area"]["required"] is True
    created = json.loads(watch_op.handler(
        {"area": {"bbox": AREA}, "grow_pct": 12, "name": "Oregon fires"}, session_id="s1"))
    assert created["ok"] and created["watch"]["feeds"] == ["fire_perimeters"]
    assert created["watch"]["conditions"]["fire_perimeters"]["grow_pct"] == 12
    assert created["first_check"]["baseline_run"] is True
    assert created["watch"]["session_id"] == "s1"

    shown = []
    monkeypatch.setattr(ext_fire, "live", lambda name, args, session_id=None: shown.append(
        (name, args, session_id)) or {"ok": True})
    annotate_op = ext_ops.find("godseye_annotate", "fire_perimeters")
    assert annotate_op is not None
    out = json.loads(annotate_op.handler({"area": {"bbox": AREA}}, session_id="s2"))
    assert out["ok"] is True
    assert shown[0][0] == "annotate_map" and shown[0][2] == "s2"
    annotations = shown[0][1]["annotations"]
    assert annotations[0]["title"] == "Cedar Creek"
    assert annotations[0]["links"][0]["label"] == "InciWeb"
