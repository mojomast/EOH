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

from gev import hermes_tools, router  # noqa: E402
from gev.selection_eval import load_golden, score  # noqa: E402

ROW = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \| (A|later) \| (.*) \|$")
# Re-measured after godseye_investigation_export landed on feat/v0.2.
# baseline = that catalog (absorbed names included, view/query absent).
# after = the catalog the model sees (18 names dropped, two composable tools added).
# Filled in by test_measure once measure_schemas.py has been run; the assertions
# below check the relationship, and the PR body records the token counts.


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
    hidden = {t.legacy for t in router.CATALOG if t.phase == "A"}
    assert hidden.isdisjoint(provided)
    assert legacy - hidden == provided - {"godseye_view", "godseye_query"}
    assert "godseye_investigation_export" in provided
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
    assert set(ctx.tools) == set(yaml.safe_load((ROOT / "plugin.yaml").read_text())["provides_tools"])
    retired = []
    for target in router.CATALOG:
        if target.phase == "A":
            retired.append(target.legacy)
            assert target.legacy not in ctx.tools, target.legacy
            assert router.absorbed(target.legacy)
            assert target.composable in {"godseye_view", "godseye_query"}
        else:
            assert target.legacy in ctx.tools, target.legacy
            assert not router.absorbed(target.legacy)
            desc = ctx.tools[target.legacy]["schema"]["description"]
            assert not desc.startswith("deprecated:"), target.legacy
    assert len(retired) == 18
    for name, tool in ctx.tools.items():
        blob = json.dumps(tool["schema"])
        assert "deprecated: use godseye_" not in blob
        for old in retired:
            assert old not in blob, (name, old)
    for name in ("godseye_view", "godseye_query"):
        schema = ctx.tools[name]["schema"]
        assert schema["parameters"]["required"] == ["op"]
        assert schema["parameters"]["properties"]["op"]["enum"] == router.phase_a_ops(name)


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
    for target in router.CATALOG:
        if target.phase != "A":
            continue
        args = samples[(target.composable, target.op)]
        seen.clear()
        ctx.tools[target.composable]["handler"]({"op": target.op, **args}, session_id="s")
        router.dispatch_legacy(target.legacy, dict(args), session_id="s")
        assert len(seen) == 2, target.legacy
        kind, name, forwarded, sid = seen[0]
        assert seen[0] == seen[1]
        assert kind == target.kind and sid == "s"
        expect_name = target.bare if target.kind != "extra" else target.legacy
        assert name == expect_name
        assert "op" not in forwarded and "here" not in forwarded and "style" not in forwarded


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
        assert spec.op == expect["op"] and spec.phase == "A"
        assert "op" not in forwarded


def test_retired_name_is_not_a_registered_tool():
    missing = json.loads(router.dispatch_legacy("godseye_doctor", {}))
    assert missing["code"] == "unknown_tool"


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
        if target.phase == "A":
            assert target.legacy not in text, target.legacy


def test_measure_schemas_shrinks_the_catalog():
    sys.path.insert(0, str(ROOT / "evals"))
    import measure_schemas
    report = measure_schemas.measure()
    assert report["baseline"]["tools"] == report["after"]["tools"] + 16
    assert report["after"]["tools"] == len(yaml.safe_load((ROOT / "plugin.yaml").read_text())["provides_tools"])
    assert report["composable_only"]["tools"] == 2
    assert report["phase_a_legacy"]["tools"] == 18
    assert report["composable_only"]["bytes"] < report["phase_a_legacy"]["bytes"]
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
