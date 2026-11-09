"""Phase A composable tools: schemas, router dispatch, alias parity, golden set."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import ext_ops, hermes_tools, router  # noqa: E402
from gev.selection_eval import load_golden, score  # noqa: E402

ROW = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \| (A|B) \| (.*) \|$")
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


def _ctx():
    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    return ctx


def _type_ok(kind: str, value) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True


def _prop_ok(prop: dict, value) -> bool:
    if "anyOf" in prop:
        return any(_prop_ok(branch, value) for branch in prop["anyOf"])
    if "enum" in prop and value not in prop["enum"]:
        return False
    kind = prop.get("type")
    if kind and not _type_ok(kind, value):
        return False
    if kind == "object" and isinstance(value, dict):
        return _schema_ok({"type": "object", "properties": prop.get("properties") or {},
                           "additionalProperties": prop.get("additionalProperties", True)}, value) is None
    if kind == "array" and isinstance(value, list) and isinstance(prop.get("items"), dict):
        return all(_prop_ok(prop["items"], item) for item in value)
    return True


def _schema_ok(schema: dict, args: dict):
    if not isinstance(args, dict):
        return "not an object"
    props = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        extra = set(args) - set(props)
        if extra:
            return f"unknown {sorted(extra)}"
    for key in schema.get("required") or []:
        if key not in args:
            return f"missing {key}"
    for key, value in args.items():
        if key not in props:
            continue
        if not _prop_ok(props[key], value):
            return f"bad {key}"
    return None


def test_catalog_matches_plugin_yaml_and_handler_family():
    provided = set(yaml.safe_load((ROOT / "plugin.yaml").read_text())["provides_tools"])
    legacy = {t.legacy for t in router.CATALOG}
    assert provided == COMPOSABLE
    assert legacy.isdisjoint(provided)
    assert "timeline" in router.phase_a_ops("godseye_nav")
    assert "timeline" not in router.RESERVED_OPS.get("godseye_nav", ())
    assert router.BY_LEGACY["godseye_live_get_current_view_state"].op == "state"
    assert router.BY_LEGACY["godseye_investigation_export"].composable == "godseye_scene"
    assert len(legacy) == len(router.CATALOG)
    live = {"godseye_live_" + t["name"] for t in hermes_tools.load_live_catalog()}
    mcp = {"godseye_" + t["name"] for t in hermes_tools.load_catalog()}
    extra = set(hermes_tools.EXTRA_TOOLS)
    for target in router.CATALOG:
        if target.legacy in live:
            assert target.kind == "live", target.legacy
        elif target.legacy in mcp:
            assert target.kind == "mcp", target.legacy
        elif target.legacy in extra:
            assert target.kind == "extra", target.legacy
        else:
            assert target.legacy == "godseye_hud_summary" and target.kind == "hud"
    ops = {}
    for target in router.CATALOG:
        ops.setdefault(target.composable, set())
        assert target.op not in ops[target.composable], target
        ops[target.composable].add(target.op)


def test_design_doc_lists_every_tool():
    rows = []
    for line in (ROOT / "docs/COMPOSABLE-TOOLS.md").read_text().splitlines():
        match = ROW.match(line)
        if match:
            rows.append(match.groups())
    assert len(rows) == len(router.CATALOG)
    for legacy, composable, op, phase, note in rows:
        target = router.BY_LEGACY[legacy]
        assert (target.composable, target.op, target.phase, target.note) == (composable, op, phase, note)


def test_phase_a_names_are_not_registered():
    ctx = _ctx()
    assert set(ctx.tools) == COMPOSABLE
    retired = [target.legacy for target in router.CATALOG]
    for target in router.CATALOG:
        assert target.legacy not in ctx.tools, target.legacy
        assert router.absorbed(target.legacy)
    for name, tool in ctx.tools.items():
        blob = json.dumps(tool["schema"])
        assert "deprecated: use godseye_" not in blob
        for old in retired:
            assert old not in blob, (name, old)
        schema = tool["schema"]
        assert schema["parameters"]["required"] == ["op"]
        assert schema["parameters"]["properties"]["op"]["enum"] == router.phase_a_ops(name)
        if name == "godseye_nav":
            assert "timeline" in schema["parameters"]["properties"]["op"]["enum"]
            assert schema["parameters"]["properties"]["timeline"]["enum"] == [
                "set", "play", "step", "stop", "state", "list_dates"]
    feeds = ctx.tools["godseye_feeds"]["schema"]["description"]
    assert "CCTV" in feeds and "ALPR" in feeds and "not a way to identify a person" in feeds


def test_every_phase_a_op_and_alias_use_the_same_handler(monkeypatch):
    seen = []

    def wrap(kind):
        def factory(name):
            def handler(args, **kw):
                seen.append((kind, name, args, kw.get("session_id")))
                return "{}"
            return handler
        return factory

    monkeypatch.setattr(hermes_tools, "make_live_handler", wrap("live"))
    monkeypatch.setattr(hermes_tools, "make_handler", wrap("mcp"))
    monkeypatch.setattr(hermes_tools, "extra_handler", wrap("extra"))
    ctx = _ctx()
    samples = {
        ("godseye_view", "fly"): {"query": "Tokyo"},
        ("godseye_view", "move"): {"motion": "stop"},
        ("godseye_view", "zoom"): {"direction": "in", "amount": "little"},
        ("godseye_view", "globe"): {"fanout": True},
        ("godseye_view", "home"): {"action": "get"},
        ("godseye_view", "share"): {},
        ("godseye_view", "panes"): {"prune": True},
        ("godseye_query", "find_places"): {"query": "ramen in Montreal"},
        ("godseye_query", "osm"): {"what": "hospitals"},
        ("godseye_query", "context"): {},
        ("godseye_query", "what_is_here"): {"lat": 1, "lon": 2},
        ("godseye_query", "brief"): {"area": {"place": "Tokyo"}},
        ("godseye_query", "brief_and_show"): {"place": "Tokyo"},
        ("godseye_query", "analyst"): {"layers": ["flights"]},
        ("godseye_query", "military"): {"location": {"place": "San Diego"}},
        ("godseye_query", "poi"): {"name": "Sumo Ramen"},
        ("godseye_query", "resolve_area"): {"query": "Bagmati"},
        ("godseye_query", "entity"): {"scope": "selected"},
    }
    def hud(args, **kw):
        seen.append(("hud", "godseye_hud_summary", args, kw.get("session_id")))
        return "{}"

    router.bind_hud(hud)
    samples.update({
        ("godseye_view", "show"): {"area": {"place": "Tokyo"}},
        ("godseye_view", "state"): {"detail": "full"},
        ("godseye_layers", "visibility"): {"layerId": "flights", "enabled": True},
        ("godseye_layers", "menu"): {},
        ("godseye_layers", "panel"): {"panelId": "data-panel", "open": True},
        ("godseye_layers", "context_mode"): {"mode": "contacts"},
        ("godseye_layers", "style"): {"style": "surveillance"},
        ("godseye_layers", "sonar"): {"enabled": True},
        ("godseye_layers", "detection"): {"mode": "dense"},
        ("godseye_layers", "stack"): {"stack": "osm"},
        ("godseye_layers", "post"): {"bloom": {}},
        ("godseye_query", "terrain"): {"points": [{"lat": 35.0, "lon": 139.0}]},
        ("godseye_query", "features"): {"location": {"lat": 35.0, "lon": 139.0}, "kind": "named_places"},
        ("godseye_feeds", "earthquakes"): {},
        ("godseye_feeds", "fires"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "launches"): {},
        ("godseye_feeds", "aircraft_in_area"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "find_aircraft"): {"callsign": "JAL1"},
        ("godseye_feeds", "aircraft_track"): {"icao24": "abc123"},
        ("godseye_feeds", "aircraft_info"): {"icao24": "abc123"},
        ("godseye_feeds", "vessels_in_area"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "find_vessel"): {"name": "Ever Given"},
        ("godseye_feeds", "vessel_track"): {"mmsi": "123456789"},
        ("godseye_feeds", "next_pass"): {"location": {"lat": 35.0, "lon": 139.0}},
        ("godseye_feeds", "overhead"): {"location": {"lat": 35.0, "lon": 139.0}},
        ("godseye_feeds", "find_cctv"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "cctv_snapshot"): {"camera_id": "cam-1"},
        ("godseye_feeds", "alpr"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "traffic"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "weather"): {"map": "radar"},
        ("godseye_feeds", "imagery"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "cables"): {},
        ("godseye_feeds", "infrastructure"): {"kind": "dams", "area": {"place": "Tokyo"}},
        ("godseye_feeds", "flood"): {},
        ("godseye_feeds", "cyclones"): {},
        ("godseye_feeds", "fire_perimeters"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "military_sites"): {"area": {"place": "Tokyo"}},
        ("godseye_feeds", "imagery_live"): {"area": "view"},
        ("godseye_feeds", "iss_pass"): {},
        ("godseye_feeds", "satellite_pass"): {"target": "25544"},
        ("godseye_feeds", "cctv_control"): {"action": "next"},
        ("godseye_feeds", "radio"): {"action": "status"},
        ("godseye_annotate", "annotate"): {"annotations": [{"type": "pin", "latitude": 1, "longitude": 2, "label": "A"}]},
        ("godseye_annotate", "clear"): {},
        ("godseye_annotate", "draw"): {"action": "start", "kind": "box"},
        ("godseye_annotate", "list"): {},
        ("godseye_annotate", "delete"): {"id": "anno-1"},
        ("godseye_annotate", "update"): {"id": "a1", "label": "Cafe"},
        ("godseye_annotate", "filter"): {"action": "set"},
        ("godseye_annotate", "drawer"): {"action": "open"},
        ("godseye_annotate", "research"): {"mode": "light"},
        ("godseye_annotate", "overlays"): {"action": "tree"},
        ("godseye_nav", "measure"): {"measure": "distance", "points": [{"lat": 1, "lon": 2}, {"lat": 3, "lon": 4}]},
        ("godseye_nav", "isochrone"): {"minutes": [10], "mode": "walk"},
        ("godseye_nav", "fly_route"): {},
        ("godseye_nav", "street_walk"): {"walk": "state"},
        ("godseye_nav", "street_level"): {"street": "at", "lat": 45.5, "lon": -73.5},
        ("godseye_nav", "itinerary"): {"pins": ["a1", "a2"], "mode": "drive"},
        ("godseye_nav", "compare"): {"left": "wayback:2014", "right": "current"},
        ("godseye_nav", "timeline"): {"timeline": "state"},
        ("godseye_track", "track"): {"query": "JAL1"},
        ("godseye_track", "stop"): {},
        ("godseye_track", "select_nearest"): {"layerId": "flights"},
        ("godseye_track", "frame"): {"target": "flights"},
        ("godseye_track", "cockpit"): {"action": "status"},
        ("godseye_scene", "save"): {"name": "Montreal Ramen"},
        ("godseye_scene", "list"): {},
        ("godseye_scene", "load"): {"name": "Montreal Ramen"},
        ("godseye_scene", "delete"): {"name": "Montreal Ramen", "confirm": True},
        ("godseye_scene", "report"): {"name": "Montreal Ramen"},
        ("godseye_scene", "export"): {"name": "Montreal Ramen", "format": "both"},
        ("godseye_scene", "restore"): {},
        ("godseye_scene", "tour"): {"stops": [{"name": "A", "lat": 1, "lon": 2}]},
        ("godseye_scene", "tour_status"): {"jobId": "job-1"},
        ("godseye_scene", "tour_stop"): {"jobId": "job-1"},
        ("godseye_scene", "playback"): {"action": "status"},
        ("godseye_watch", "create"): {"area": {"view": True}, "feeds": ["earthquakes"]},
        ("godseye_watch", "list"): {},
        ("godseye_watch", "pause"): {"id": "w-1"},
        ("godseye_watch", "delete"): {"id": "w-1", "confirm": True},
        ("godseye_capture", "hud"): {"visible": "on"},
        ("godseye_capture", "summary"): {"context": {"place": "Tokyo"}},
        ("godseye_capture", "pane"): {},
        ("godseye_admin", "doctor"): {},
        ("godseye_admin", "batch"): {"calls": [{"tool": "godseye_admin", "args": {"op": "status"}}]},
        ("godseye_admin", "keys"): {"action": "install", "file": "/tmp/godseye-keys.env"},
        ("godseye_admin", "rebuild"): {},
        ("godseye_admin", "reload"): {},
        ("godseye_admin", "preview"): {"url": "https://example.com"},
        ("godseye_admin", "status"): {},
        ("godseye_admin", "app_start"): {},
        ("godseye_admin", "app_stop"): {},
        ("godseye_admin", "app_restart"): {},
    })
    for target in router.CATALOG:
        args = dict(samples[(target.composable, target.op)])
        legacy_args = dict(args)
        if target.op == "measure":
            legacy_args["op"] = legacy_args.pop("measure")
        elif target.op == "timeline":
            legacy_args["op"] = legacy_args.pop("timeline")
        elif target.op == "street_walk":
            legacy_args["op"] = legacy_args.pop("walk")
        elif target.op == "street_level":
            legacy_args["op"] = legacy_args.pop("street")
        seen.clear()
        ctx.tools[target.composable]["handler"]({"op": target.op, **args}, session_id="s")
        router.dispatch_legacy(target.legacy, legacy_args, session_id="s")
        assert len(seen) == 2, (target.legacy, seen)
        assert seen[0] == seen[1]
        kind, name, forwarded, sid = seen[0]
        assert kind == target.kind and sid == "s"
        if target.kind == "hud":
            expect_name = "godseye_hud_summary"
        elif target.kind == "extra":
            expect_name = target.legacy
        else:
            expect_name = target.bare
        assert name == expect_name
        assert "here" not in forwarded
        if target.op == "measure":
            assert forwarded["op"] == "distance" and "measure" not in forwarded
        elif target.op == "timeline":
            assert forwarded["op"] == "state" and "timeline" not in forwarded
        elif target.op == "street_walk":
            assert forwarded["op"] == "state" and "walk" not in forwarded
        elif target.op == "street_level":
            assert forwarded["op"] == "at" and "street" not in forwarded
        else:
            assert "op" not in forwarded
        if target.composable == "godseye_view" and target.op == "fly":
            assert "style" not in forwarded


def test_fly_style_calls_set_visual_style_after_a_successful_fly(monkeypatch):
    calls = []

    def live_call(name, args=None, session_id=None, fanout=False, **_kw):
        calls.append((name, args))
        if name == "fly_to_location":
            return {"ok": True, "result": {"label": "Tokyo"}}
        return {"ok": True, "result": {"style": (args or {}).get("style")}}

    monkeypatch.setattr("gev.composer.live_call", live_call)
    ctx = _ctx()
    out = json.loads(ctx.tools["godseye_view"]["handler"](
        {"op": "fly", "query": "Tokyo", "style": "nvg", "lat": 35.0, "lon": 139.0}, session_id="s"))
    assert [c[0] for c in calls] == ["fly_to_location", "set_visual_style"]
    assert calls[0][1]["query"] == "Tokyo" and "style" not in calls[0][1]
    assert calls[0][1]["latitude"] == 35.0 and calls[0][1]["longitude"] == 139.0
    assert calls[1][1] == {"style": "surveillance"}
    assert out["style"]["result"]["style"] == "surveillance"

    calls.clear()
    monkeypatch.setattr("gev.composer.live_call", lambda *a, **k: {"ok": False, "error": "no_pane", "message": "no pane"})
    failed = json.loads(ctx.tools["godseye_view"]["handler"]({"op": "fly", "query": "Tokyo", "style": "surveillance"}))
    assert failed["error"] == "no_pane"
    bad = json.loads(ctx.tools["godseye_view"]["handler"]({"op": "fly", "query": "Tokyo", "style": "sepia"}))
    assert bad["error"] == "bad_args" and calls == []


def test_alias_fly_matches_composable_fly_without_style(monkeypatch):
    calls = []

    def live_call(name, args=None, session_id=None, fanout=False, **_kw):
        calls.append((name, dict(args or {}), session_id, fanout))
        return {"ok": True, "result": {"label": "Tokyo"}}

    monkeypatch.setattr("gev.composer.live_call", live_call)
    ctx = _ctx()
    alias = router.dispatch_legacy(
        "godseye_live_fly_to_location",
        {"query": "Tokyo", "framing": "street", "fanout": True}, session_id="chat")
    composable = ctx.tools["godseye_view"]["handler"](
        {"op": "fly", "query": "Tokyo", "framing": "street", "fanout": True}, session_id="chat")
    assert alias == composable
    assert calls[0] == calls[1] == ("fly_to_location", {"query": "Tokyo", "framing": "street"}, "chat", True)


def test_here_resolves_the_camera_then_calls_the_original_handler(monkeypatch):
    monkeypatch.setattr(router, "camera", lambda session_id: ({"lat": 45.5, "lon": -73.6, "rangeM": 2500.0}, None))
    places = []
    monkeypatch.setattr("gev.poi.find_places", lambda args: places.append(dict(args)) or {"ok": True, "places": []})
    briefs = []

    class Client:
        def call_tool(self, name, args):
            briefs.append((name, args))
            return {"content": [{"type": "text", "text": "Situation around here."}]}

    monkeypatch.setattr("gev.mcp_client.client", lambda: Client())
    ctx = _ctx()
    ctx.tools["godseye_query"]["handler"](
        {"op": "find_places", "query": "ramen near me", "pin": True, "intent": "ramen near me"})
    assert places[0]["query"] == "ramen"
    assert places[0]["lat"] == 45.5 and places[0]["lon"] == -73.6 and places[0]["radiusM"] == 1500
    assert "near" not in places[0] and "here" not in places[0]
    ctx.tools["godseye_query"]["handler"]({"op": "brief", "here": True})
    assert briefs[0][0] == "situation_brief"
    assert briefs[0][1]["area"]["lat"] == 45.5
    assert briefs[0][1]["area"]["radius_km"] == 2.5


def test_composable_query_refuses_person_tracking_before_dispatch(monkeypatch):
    monkeypatch.setattr("gev.poi.find_places", lambda args: (_ for _ in ()).throw(AssertionError("poi")))
    monkeypatch.setattr("gev.mcp_client.client", lambda: (_ for _ in ()).throw(AssertionError("mcp")))
    ctx = _ctx()
    out = json.loads(ctx.tools["godseye_query"]["handler"](
        {"op": "brief", "area": {"place": "track someone in Paris"}}))
    assert "Refused" in out["error"]
    assert out["code"] == "refused"


def test_unknown_op_does_not_call_a_handler(monkeypatch):
    def factory(name):
        def handler(args, **_kw):
            raise AssertionError(name)
        return handler

    monkeypatch.setattr(hermes_tools, "make_live_handler", factory)
    monkeypatch.setattr(hermes_tools, "make_handler", factory)
    monkeypatch.setattr(hermes_tools, "extra_handler", factory)
    ctx = _ctx()
    out = json.loads(ctx.tools["godseye_view"]["handler"]({"op": "style"}))
    assert out["error"] == "unknown_op" and "fly" in out["fix"]
    missing = json.loads(ctx.tools["godseye_query"]["handler"]({}))
    assert missing["error"] == "unknown_op"
    assert "timeline" in ctx.tools["godseye_nav"]["schema"]["parameters"]["properties"]["op"]["enum"]


def test_golden_set_matches_schemas_and_the_router(monkeypatch):
    monkeypatch.setattr(router, "camera", lambda session_id: ({"lat": 35.68, "lon": 139.69, "rangeM": 8000.0}, None))
    ctx = _ctx()
    data = load_golden()
    ids = [task["id"] for task in data["tasks"]]
    assert ids == list(dict.fromkeys(ids)) and len(ids) >= 4
    phrases = {task["task"] for task in data["tasks"]}
    for phrase in ("fly to Tokyo NVG", "what am I looking at", "ramen near me", "brief me on this area"):
        assert phrase in phrases
    for task in data["tasks"]:
        expect = task["expect"]
        schema = ctx.tools[expect["tool"]]["schema"]["parameters"]
        assert _schema_ok(schema, expect["arguments"]) is None, task["id"]
        assert score(task, expect["tool"], expect["arguments"]) is None, task["id"]
        spec, forwarded, extra, err = router.prepare(expect["tool"], dict(expect["arguments"]), "s")
        assert err is None, (task["id"], err)
        assert spec.op == expect["op"]
        if isinstance(spec, ext_ops.ExtOp):
            assert spec.tool == expect["tool"]
        else:
            assert spec.phase in {"A", "B"}
        if expect["op"] == "measure":
            assert forwarded.get("op") == expect["arguments"]["measure"]
            assert "measure" not in forwarded
        elif expect["op"] == "timeline":
            assert forwarded.get("op") == expect["arguments"]["timeline"]
            assert "timeline" not in forwarded
        elif expect["op"] == "street_walk":
            assert forwarded.get("op") == expect["arguments"]["walk"]
            assert "walk" not in forwarded
        elif expect["op"] == "street_level":
            assert forwarded.get("op") == expect["arguments"]["street"]
            assert "street" not in forwarded
        else:
            assert "op" not in forwarded
    covered = {(task["expect"]["tool"], task["expect"]["op"]) for task in data["tasks"]}
    for target in router.CATALOG:
        assert (target.composable, target.op) in covered, target.legacy


def test_retired_name_is_not_a_registered_tool():
    missing = json.loads(router.dispatch_legacy("godseye_not_a_tool", {}))
    assert missing["code"] == "unknown_tool"
    doctor = json.loads(router.dispatch_legacy("godseye_doctor", {}))
    assert doctor.get("code") != "unknown_tool"


def test_system_prompt_teaches_the_new_tools_first():
    text = hermes_tools.SYSTEM_PROMPT
    assert "godseye_view" in text and "godseye_query" in text
    assert "Legacy" not in text
    assert "godseye_live_fly_to_location" not in text
    assert len(text) <= 1500


def test_skill_teaches_new_tools_and_keeps_the_policy():
    text = (ROOT / "skills/godseye-analyst/SKILL.md").read_text()
    assert text.startswith("---\nname: godseye-analyst")
    assert "Public assets and events only" in text and "no_pane" in text and "brief_and_show" in text
    assert "godseye_view" in text and "godseye_query" in text and "op=context" in text
    assert "dispatch_legacy" in text
    assert "Legacy names" not in text
    for target in router.CATALOG:
        assert target.legacy not in text, target.legacy


def test_feeds_refuse_person_tracking_before_dispatch(monkeypatch):
    monkeypatch.setattr("gev.mcp_client.client", lambda: (_ for _ in ()).throw(AssertionError("mcp")))
    ctx = _ctx()
    out = json.loads(ctx.tools["godseye_feeds"]["handler"](
        {"op": "find_cctv", "area": {"place": "track someone in Paris"}}))
    assert "Refused" in out["error"] and out["code"] == "refused"


def test_measure_schemas_shrinks_the_catalog():
    sys.path.insert(0, str(ROOT / "evals"))
    import measure_schemas
    report = measure_schemas.measure()
    assert report["after"]["tools"] == 11
    assert report["baseline"]["tools"] == 94  # phase-B reconstruction, including overlays; unsafe key import retired
    assert report["after"]["tools"] == len(yaml.safe_load((ROOT / "plugin.yaml").read_text())["provides_tools"])
    assert report["after"]["bytes"] < report["baseline"]["bytes"]
    assert report["after"]["tokens"]["o200k_base"] < report["baseline"]["tokens"]["o200k_base"]
    assert report["after"]["tokens"]["cl100k_base"] < report["baseline"]["tokens"]["cl100k_base"]


def test_live_selection_script_skips_without_a_key_and_in_ci():
    script = ROOT / "evals" / "run_selection.py"
    env = {k: v for k, v in os.environ.items()
           if k not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CI", "GITHUB_ACTIONS"}}
    proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "skip:" in proc.stdout.lower()
    assert "sk-" not in proc.stdout
    env["CI"] = "true"
    env["OPENAI_API_KEY"] = "sk-test-not-real"
    proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "CI" in proc.stdout
    assert "sk-test-not-real" not in proc.stdout + proc.stderr
