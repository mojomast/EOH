"""Extension seam: an ext module adds an op without a router edit."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import ext_ops, hermes_tools, router  # noqa: E402

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


def _fake_module(monkeypatch, op, name="gev.ext_fake"):
    module = types.ModuleType(name)
    module.EXT_OPS = [op]
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _echo(args, **kw):
    return json.dumps({"ok": True, "args": args, "session_id": kw.get("session_id")})


def test_ext_module_adds_an_op_to_an_existing_tool(monkeypatch):
    _fake_module(monkeypatch, ext_ops.ExtOp(
        tool="godseye_nav",
        op="hello",
        handler=_echo,
        props={"target": {"type": "string", "description": "Who to greet."}},
        blurb="Say hello without leaving the map.",
    ))

    ctx = _ctx()
    assert set(ctx.tools) == COMPOSABLE
    assert len(ctx.tools) == 11

    schema = ctx.tools["godseye_nav"]["schema"]["parameters"]
    assert "hello" in schema["properties"]["op"]["enum"]
    assert schema["properties"]["target"]["type"] == "string"

    out = json.loads(ctx.tools["godseye_nav"]["handler"](
        {"op": "hello", "target": "Ada", "unused": True}, session_id="chat-1"))
    assert out == {"ok": True, "args": {"target": "Ada"}, "session_id": "chat-1"}


def test_duplicate_ext_op_with_an_existing_op_is_refused(monkeypatch):
    _fake_module(monkeypatch, ext_ops.ExtOp(
        tool="godseye_nav",
        op="measure",
        handler=_echo,
        props={},
        blurb="Collides with the built-in measure op.",
    ))

    with pytest.raises(ValueError, match="measure"):
        ext_ops.discover()


def test_duplicate_ext_op_across_modules_is_refused(monkeypatch):
    op = ext_ops.ExtOp(
        tool="godseye_nav",
        op="hello",
        handler=_echo,
        props={},
        blurb="First.",
    )
    _fake_module(monkeypatch, op, name="gev.ext_fake_a")
    _fake_module(monkeypatch, op, name="gev.ext_fake_b")

    with pytest.raises(ValueError, match="hello"):
        ext_ops.discover()


def test_unknown_tool_is_refused(monkeypatch):
    _fake_module(monkeypatch, ext_ops.ExtOp(
        tool="godseye_twelfth",
        op="hello",
        handler=_echo,
        props={},
        blurb="There is no twelfth tool.",
    ))

    with pytest.raises(ValueError, match="godseye_twelfth"):
        ext_ops.discover()


def test_import_failure_is_skipped_and_recorded_for_the_doctor(monkeypatch):
    monkeypatch.setitem(sys.modules, "gev.ext_broken", None)

    found = ext_ops.discover()
    assert all(not record.handler.__module__.endswith("ext_broken") for record in found)
    assert "gev.ext_broken" in ext_ops.errors()
