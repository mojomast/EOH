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
# Measured on this tree before phase A (compact schema JSON, tiktoken o200k_base).
BASELINE_BYTES = 128269
BASELINE_O200K = 30133


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
    assert legacy == provided - {"godseye_view", "godseye_query"}
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


def test_phase_a_aliases_are_deprecated_and_others_are_not():
    ctx = _ctx()
    assert set(ctx.tools) == set(yaml.safe_load((ROOT / "plugin.yaml").read_text())["provides_tools"])
    for target in router.CATALOG:
        desc = ctx.tools[target.legacy]["schema"]["description"]
        note = router.deprecation_note(target.legacy)
        if target.phase == "A":
            assert note and desc.startswith(note + "."), target.legacy
            assert target.composable in {"godseye_view", "godseye_query"}
        else:
            assert note is None
            assert not desc.startswith("deprecated:"), target.legacy
    for name in ("godseye_view", "godseye_query"):
        schema = ctx.tools[name]["schema"]
        assert schema["parameters"]["required"] == ["op"]
        assert schema["parameters"]["properties"]["op"]["enum"] == router.phase_a_ops(name)
        assert "deprecated:" not in schema["description"][:20]


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
        ctx.tools[target.legacy]["handler"](dict(args), session_id="s")
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
    alias = ctx.tools["godseye_live_fly_to_location"]["handler"](
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


def test_system_prompt_teaches_the_new_tools_first():
    text = hermes_tools.SYSTEM_PROMPT
    assert "godseye_view" in text and "godseye_query" in text
    assert text.index("godseye_view") < text.index("Legacy")
    assert len(text) <= 1500


def test_skill_teaches_new_tools_and_keeps_the_policy():
    text = (ROOT / "skills/godseye-analyst/SKILL.md").read_text()
    assert text.startswith("---\nname: godseye-analyst")
    assert "Public assets and events only" in text and "no_pane" in text and "godseye_brief_and_show" in text
    assert text.index("godseye_view") < text.index("Legacy names")
    assert "godseye_query" in text and "op=context" in text


def test_measure_schemas_reconstructs_the_prechange_size():
    sys.path.insert(0, str(ROOT / "evals"))
    import measure_schemas
    report = measure_schemas.measure()
    assert report["baseline"]["tools"] == 97
    assert report["after"]["tools"] == 99
    assert report["baseline"]["bytes"] == BASELINE_BYTES
    assert report["composable_only"]["bytes"] < report["phase_a_legacy_without_notes"]["bytes"]
    assert report["after"]["bytes"] > report["baseline"]["bytes"]
    if report["baseline"]["tokens"]:
        assert report["baseline"]["tokens"]["o200k_base"] == BASELINE_O200K
        assert report["composable_only"]["tokens"]["o200k_base"] < report["phase_a_legacy_without_notes"]["tokens"]["o200k_base"]


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
