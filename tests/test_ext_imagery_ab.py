"""godseye_nav op=imagery_ab: before-and-after recent imagery."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import hermes_tools  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "recent_imagery_days.json"
BBOX = [-100.0, 30.0, -99.6, 30.4]
COMPOSABLE = {
    "godseye_view", "godseye_query", "godseye_layers", "godseye_feeds",
    "godseye_annotate", "godseye_nav", "godseye_track", "godseye_scene",
    "godseye_watch", "godseye_capture", "godseye_admin",
}


@pytest.fixture
def mod():
    from gev import ext_imagery_ab
    return ext_imagery_ab


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw

    def register_system_prompt_section(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass


def _days():
    return json.loads(FIXTURE.read_text())


def _wire(monkeypatch, mod, *, mcp_result=None, live_result=None):
    mcp_calls = []
    live_calls = []

    def fake_mcp(name, args):
        mcp_calls.append((name, args))
        return _days() if mcp_result is None else mcp_result

    def fake_live(name, args, **kw):
        live_calls.append((name, args, kw))
        return {"ok": True, "shown": {"date": args.get("dateRange", {}).get("from")}} if live_result is None else live_result

    monkeypatch.setattr(mod, "now", lambda: date(2026, 11, 6))
    monkeypatch.setattr(mod, "mcp", fake_mcp)
    monkeypatch.setattr(mod, "live", fake_live)
    return mcp_calls, live_calls


def _run(mod, args):
    return json.loads(mod.handler(args, session_id="chat-1"))


def test_auto_pick_uses_earliest_and_latest_clear_days(monkeypatch, mod):
    mcp_calls, live_calls = _wire(monkeypatch, mod)

    out = _run(mod, {"bbox": BBOX})

    assert out["ok"] is True
    assert out["before"] == {"date": "2026-10-21", "cloud": 11, "id": "S30-2026-10-21"}
    assert out["after"] == {"date": "2026-11-02", "cloud": 7, "id": "S30-2026-11-02"}
    assert out["mode"] == "swipe"
    assert out["drape_recommended"] is True
    assert mcp_calls == [("get_recent_imagery", {"area": {"bbox": BBOX}})]
    assert [call[0] for call in live_calls] == ["find_imagery", "find_imagery"]
    assert live_calls[0][1]["dateRange"] == {"from": "2026-10-21", "to": "2026-10-21"}
    assert live_calls[1][1]["dateRange"] == {"from": "2026-11-02", "to": "2026-11-02"}
    assert [action["args"]["dateRange"]["from"] for action in out["actions"]] == [
        "2026-10-21", "2026-11-02",
    ]


def test_place_uses_the_area_place_and_result_bbox(monkeypatch, mod):
    mcp_calls, _live_calls = _wire(monkeypatch, mod)

    out = _run(mod, {"place": "Test Basin"})

    assert out["ok"] is True
    assert mcp_calls == [("get_recent_imagery", {"area": {"place": "Test Basin"}})]
    assert out["drape_recommended"] is True


def test_mcp_content_text_wrapper_is_parsed(monkeypatch, mod):
    wrapped = {"content": [{"type": "text", "text": json.dumps(_days())}]}
    _wire(monkeypatch, mod, mcp_result=wrapped)

    out = _run(mod, {"bbox": BBOX})

    assert out["ok"] is True
    assert out["before"]["date"] == "2026-10-21"
    assert out["after"]["date"] == "2026-11-02"


def test_explicit_before_and_after_dates_are_honored(monkeypatch, mod):
    _wire(monkeypatch, mod)

    out = _run(mod, {
        "bbox": BBOX,
        "before": "2026-10-18",
        "after": "2026-10-28",
        "max_cloud": 100,
    })

    assert out["ok"] is True
    assert out["before"] == {"date": "2026-10-18", "cloud": 78, "id": "S30-2026-10-18"}
    assert out["after"] == {"date": "2026-10-28", "cloud": 62, "id": "S30-2026-10-28"}
    assert out["mode"] == "swipe"


def test_no_day_under_max_cloud_reports_the_best_available(monkeypatch, mod):
    _wire(monkeypatch, mod)

    out = _run(mod, {"bbox": BBOX, "max_cloud": 3})

    assert out["ok"] is False
    assert out["error"] == "no_clear_day"
    assert out["best_available"] == {
        "date": "2026-11-02", "cloud": 7, "id": "S30-2026-11-02",
    }
    assert "7" in out["message"]
    assert "max_cloud" in out["fix"]


@pytest.mark.parametrize("bbox", [
    [10.0, 20.0, 5.0, 25.0],
    [-100.0, 30.0, -99.6],
    [-200.0, 30.0, -199.0, 30.4],
])
def test_bbox_validation_refuses_a_bad_box(monkeypatch, mod, bbox):
    mcp_calls, live_calls = _wire(monkeypatch, mod)

    out = _run(mod, {"bbox": bbox})

    assert out["ok"] is False
    assert out["error"] == "bad_args"
    assert mcp_calls == []
    assert live_calls == []


def test_op_is_merged_into_nav_without_a_twelfth_tool(monkeypatch, mod):
    ctx = Ctx()
    hermes_tools.register_tools(ctx)

    assert set(ctx.tools) == COMPOSABLE
    assert len(ctx.tools) == 11
    schema = ctx.tools["godseye_nav"]["schema"]["parameters"]
    assert "imagery_ab" in schema["properties"]["op"]["enum"]
    assert schema["properties"]["bbox"]["type"] == "array"
    assert schema["properties"]["max_cloud"]["type"] == "number"
