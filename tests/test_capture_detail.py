"""P15: godseye_capture accepts detail=concise|full (concise by default)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import hermes_tools, router  # noqa: E402

COMPOSABLE = {
    "godseye_view", "godseye_query", "godseye_layers", "godseye_feeds",
    "godseye_annotate", "godseye_nav", "godseye_track", "godseye_scene",
    "godseye_watch", "godseye_capture", "godseye_admin",
}
BLOB = "data:image/png;base64," + "A" * 2400


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


def _detail_prop(schema: dict) -> dict | None:
    params = schema["parameters"]
    prop = (params.get("properties") or {}).get("detail")
    if prop is not None:
        return prop
    for pattern, candidate in (params.get("patternProperties") or {}).items():
        if pattern in ("^detail$", "detail"):
            return candidate
    return None


def _pane_result() -> dict:
    return {
        "ok": True,
        "action": "hermes_pane_shot",
        "mime": "image/png",
        "dataUrl": BLOB,
        "width": 800,
        "height": 600,
        "includesDrawer": False,
    }


def _fake_live(monkeypatch):
    calls = []

    def live_call(name, args=None, session_id=None, fanout=False, **_kw):
        calls.append((name, dict(args or {})))
        return _pane_result()

    monkeypatch.setattr("gev.composer.live_call", live_call)
    return calls


def test_capture_schema_exposes_detail_concise_and_full():
    ctx = _ctx()
    assert set(ctx.tools) == COMPOSABLE
    assert len(ctx.tools) == 11
    schema = ctx.tools["godseye_capture"]["schema"]
    prop = _detail_prop(schema)
    assert prop is not None, "godseye_capture schema does not accept detail"
    assert prop["type"] == "string"
    assert prop["enum"] == ["concise", "full"]
    assert prop["default"] == "concise"


def test_pane_detail_defaults_to_concise_and_full_restores_the_image(monkeypatch):
    calls = _fake_live(monkeypatch)
    ctx = _ctx()
    handler = ctx.tools["godseye_capture"]["handler"]

    concise = json.loads(handler({"op": "pane"}, session_id="s"))
    assert concise["ok"] is True
    assert concise["dataUrl"] == {"image": "omitted", "bytes": len(BLOB)}
    assert concise["detail"] == "concise"
    assert "detail=full" in concise["hint"]
    assert calls == [("hermes_pane_shot", {})]

    calls.clear()
    full = json.loads(handler({"op": "pane", "detail": "full"}, session_id="s"))
    assert full["ok"] is True
    assert full["dataUrl"] == BLOB
    assert "detail" not in full
    assert calls == [("hermes_pane_shot", {})]


def test_pane_rejects_an_unknown_detail_value(monkeypatch):
    _fake_live(monkeypatch)
    ctx = _ctx()
    out = json.loads(ctx.tools["godseye_capture"]["handler"](
        {"op": "pane", "detail": "verbose"}, session_id="s"))
    assert out["ok"] is False
    assert out["error"] == "bad_args"
    assert "detail" in out["message"]
    assert "concise, full" in out["fix"]


def test_capture_alias_uses_the_same_detail_default(monkeypatch):
    _fake_live(monkeypatch)
    concise = json.loads(router.dispatch_legacy("godseye_pane_shot", {}, session_id="s"))
    assert concise["dataUrl"] == {"image": "omitted", "bytes": len(BLOB)}
    full = json.loads(router.dispatch_legacy(
        "godseye_pane_shot", {"detail": "full"}, session_id="s"))
    assert full["dataUrl"] == BLOB
