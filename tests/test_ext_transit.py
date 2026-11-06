"""GTFS-RT transit ops on godseye_feeds and godseye_track."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import ext_ops, ext_transit, hermes_tools, router  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "transit_vehicles.json").read_text())
COMPOSABLE = {
    "godseye_view", "godseye_query", "godseye_layers", "godseye_feeds",
    "godseye_annotate", "godseye_nav", "godseye_track", "godseye_scene",
    "godseye_watch", "godseye_capture", "godseye_admin",
}


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw

    def register_system_prompt_section(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass


def _handler(tool: str):
    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    assert set(ctx.tools) == COMPOSABLE
    assert len(ctx.tools) == 11
    return ctx.tools[tool]["handler"]


def _fake_live(calls, payload=None, set_result=None, track_result=None):
    def fake(name, args=None, session_id=None, **kwargs):
        args = dict(args or {})
        calls.append({"name": name, "args": args, "session_id": session_id})
        if name == "set_layer_visibility":
            return set_result if set_result is not None else {"ok": True}
        if name == "analyst_query":
            return payload if payload is not None else FIXTURE
        if name == "track_entity":
            return track_result if track_result is not None else {"ok": True, "pane": "pane-1"}
        return {"ok": True}

    return fake


def test_ops_are_registered_on_existing_tools():
    records = {(record.tool, record.op) for record in ext_ops.discover()}
    assert ("godseye_feeds", "transit") in records
    assert ("godseye_track", "follow_vehicle") in records
    assert len({tool for tool, _op in records}) <= 11

    props = next(record.props for record in ext_ops.discover()
                 if (record.tool, record.op) == ("godseye_feeds", "transit"))
    assert props["action"]["enum"] == ["routes", "vehicles"]
    assert props["action"]["required"] is True


def test_vehicles_turn_on_the_layer_then_read_analyst(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = json.loads(_handler("godseye_feeds")({
        "op": "transit", "action": "vehicles", "place": "Rio de Janeiro",
    }, session_id="chat-1"))

    assert out["ok"] is True
    assert out["action"] == "vehicles"
    assert out["region"] == "Rio de Janeiro"
    assert out["count"] == 5
    assert len(out["vehicles"]) == 5
    assert set(out["vehicles"][0]) == {"id", "route", "lat", "lon", "heading", "updated"}
    assert out["vehicles"][0] == {
        "id": "rio-bus-1001", "route": "100", "lat": -22.9711, "lon": -43.1822,
        "heading": 92, "updated": "2026-11-05T23:40:00Z",
    }

    assert calls[0]["name"] == "set_layer_visibility"
    assert calls[0]["args"] == {"layerId": "transit", "enabled": True}
    assert calls[0]["session_id"] == "chat-1"
    assert calls[1]["name"] == "analyst_query"
    assert calls[1]["args"]["layers"] == ["transit"]
    assert calls[1]["args"]["scope"] == {"kind": "region", "name": "Rio de Janeiro"}


def test_vehicles_are_capped_at_50_with_the_full_count(monkeypatch):
    calls = []
    rows = []
    for index in range(60):
        row = dict(FIXTURE["result"]["items"][index % 5])
        row["id"] = f"rio-bus-{index:04d}"
        rows.append(row)
    payload = {"ok": True, "result": {"items": rows}}
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls, payload=payload))

    out = json.loads(_handler("godseye_feeds")({
        "op": "transit", "action": "vehicles", "place": "rio",
    }, session_id="chat-2"))

    assert out["ok"] is True
    assert out["count"] == 60
    assert len(out["vehicles"]) == 50


def test_routes_group_the_vehicles_by_route(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = json.loads(_handler("godseye_feeds")({
        "op": "transit", "action": "routes", "place": "Rio de Janeiro",
    }, session_id="chat-3"))

    assert out["ok"] is True
    assert out["action"] == "routes"
    assert out["count"] == 3
    assert all(set(route) == {"route_id", "name", "vehicles"} for route in out["routes"])
    by_id = {route["route_id"]: route for route in out["routes"]}
    assert by_id["100"]["vehicles"] == 2
    assert by_id["100"]["name"] == "100 Central - Copacabana"
    assert by_id["483"]["vehicles"] == 2
    assert by_id["201"]["vehicles"] == 1


def test_bbox_inside_coverage_uses_a_radius_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = json.loads(_handler("godseye_feeds")({
        "op": "transit", "action": "vehicles", "bbox": [-23.0, -43.30, -22.90, -43.20],
    }, session_id="chat-4"))

    assert out["ok"] is True
    scope = next(call for call in calls if call["name"] == "analyst_query")["args"]["scope"]
    assert scope["kind"] == "radius"
    assert scope["center"] == {"lat": -22.95, "lon": -43.25}
    assert scope["km"] > 0


def test_unknown_place_lists_the_covered_regions(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = json.loads(_handler("godseye_feeds")({
        "op": "transit", "action": "vehicles", "place": "Tokyo",
    }, session_id="chat-5"))

    assert out["ok"] is False
    assert out["error"] == "no_transit_coverage"
    assert "Rio de Janeiro" in out["message"]
    assert out["covered_regions"] == ["Rio de Janeiro"]
    assert calls == []


def test_bbox_outside_coverage_lists_the_covered_regions(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = json.loads(_handler("godseye_feeds")({
        "op": "transit", "action": "routes", "bbox": [-10.0, -10.0, -9.0, -9.0],
    }, session_id="chat-6"))

    assert out["ok"] is False
    assert out["error"] == "no_transit_coverage"
    assert "Rio de Janeiro" in out["message"]
    assert calls == []


def test_a_failed_analyst_read_is_reported(monkeypatch):
    calls = []
    payload = {"ok": False, "error": "LAYER_OFF",
               "message": "The transit layer is not ready."}
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls, payload=payload))

    out = json.loads(_handler("godseye_feeds")({
        "op": "transit", "action": "vehicles", "place": "Rio de Janeiro",
    }, session_id="chat-7"))

    assert out["ok"] is False
    assert out["error"] == "LAYER_OFF"
    assert "transit layer" in out["message"]


def test_follow_vehicle_is_unsupported_without_live_transit_tracking(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = json.loads(_handler("godseye_track")({
        "op": "follow_vehicle", "vehicle_id": "rio-bus-1001",
    }, session_id="chat-8"))

    assert out["ok"] is False
    assert out["error"] == "unsupported"
    assert out["code"] == "unsupported"
    assert out["action"] == "follow_vehicle"
    assert out["vehicle_id"] == "rio-bus-1001"
    assert out["layer"] == "transit"
    assert "transit" in out["message"].lower()
    assert out["fix"]
    assert out["supported_layers"] == ["ais-live-vessels", "flights", "military", "satellites"]
    assert calls == []


def test_follow_vehicle_missing_vehicle_id_stays_bad_args(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = json.loads(_handler("godseye_track")({
        "op": "follow_vehicle",
    }, session_id="chat-8b"))

    assert out["ok"] is False
    assert out["error"] == "bad_args"
    assert out["code"] == "bad_args"
    assert out["fix"]
    assert calls == []


def test_bundled_live_contract_does_not_advertise_transit_tracking():
    assert ext_transit.TRANSIT_LAYER not in ext_transit.supported_track_layers()
    assert ext_transit.supports_transit_track() is False


def test_follow_vehicle_tracks_when_the_live_pane_advertises_transit(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))

    out = ext_transit.follow_vehicle({
        "vehicle_id": "rio-bus-1001",
    }, live=ext_transit.live, session_id="chat-8c", track_layers={"transit"})

    assert out["ok"] is True
    assert out["action"] == "follow_vehicle"
    assert out["vehicle_id"] == "rio-bus-1001"
    assert out["layer"] == "transit"
    names = [call["name"] for call in calls]
    assert names == ["set_layer_visibility", "track_entity"]
    assert calls[1]["args"] == {"query": "rio-bus-1001", "layerId": "transit"}
    assert calls[1]["session_id"] == "chat-8c"


def test_handler_follows_transit_when_capability_is_present(monkeypatch):
    calls = []
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls))
    monkeypatch.setattr(ext_transit, "supported_track_layers",
                        lambda: frozenset({"transit", "flights"}))

    out = json.loads(_handler("godseye_track")({
        "op": "follow_vehicle", "vehicle_id": "rio-bus-1001",
    }, session_id="chat-8d"))

    assert out["ok"] is True
    assert [call["name"] for call in calls] == ["set_layer_visibility", "track_entity"]


def test_follow_vehicle_surfaces_a_track_failure(monkeypatch):
    calls = []
    failed = {"ok": False, "error": "not_found", "message": "No such transit vehicle."}
    monkeypatch.setattr(ext_transit, "live", _fake_live(calls, track_result=failed))

    out = ext_transit.follow_vehicle({
        "vehicle_id": "missing",
    }, live=ext_transit.live, session_id="chat-9", track_layers={"transit"})

    assert out["ok"] is False
    assert out["error"] == "not_found"
    assert out["vehicle_id"] == "missing"
    assert out["fix"]


def test_stop_stays_on_the_existing_stop_op():
    assert router.BY_LEGACY["godseye_live_stop_tracking"].composable == "godseye_track"
    assert router.BY_LEGACY["godseye_live_stop_tracking"].op == "stop"
