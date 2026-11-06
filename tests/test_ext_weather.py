"""godseye_layers op=preset weather: live layer calls and a cyclone summary."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gev import ext_ops, ext_weather, hermes_tools, router  # noqa: E402

FIXTURE = Path(__file__).with_name("fixtures") / "cyclones.json"


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw

    def register_system_prompt_section(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _fakes(monkeypatch):
    events: list[tuple[str, dict]] = []

    def live_call(name, args=None, session_id=None, fanout=False):
        events.append((name, dict(args or {})))
        return {"ok": True, "result": {"name": name}}

    def mcp_call(name, args):
        events.append((name, dict(args or {})))
        return {"structuredContent": _payload()}

    monkeypatch.setattr(ext_weather, "live_call", live_call)
    monkeypatch.setattr(ext_weather, "mcp_call", mcp_call)
    return events


def _run(args: dict) -> dict:
    record, forwarded, _extra, error = router.prepare("godseye_layers", dict(args))
    assert error is None, error
    assert record is not None
    return json.loads(router.invoke(record, forwarded))


def test_weather_preset_turns_on_the_four_layers_in_order(monkeypatch):
    events = _fakes(monkeypatch)

    out = _run({"op": "preset", "preset": "weather"})

    layer_calls = [event for event in events if event[0] == "set_layer_visibility"]
    assert layer_calls == [
        ("set_layer_visibility", {"layerId": "wind", "enabled": True}),
        ("set_layer_visibility", {"layerId": "weather-radar", "enabled": True}),
        ("set_layer_visibility", {"layerId": "weather-satellite", "enabled": True}),
        ("set_layer_visibility", {"layerId": "weather-cyclones", "enabled": True}),
    ]
    assert events[-1] == ("get_cyclones", {})
    assert out["layers_on"] == ["wind", "weather-radar", "weather-satellite", "weather-cyclones"]
    assert out["basemap"] is None
    assert len(out["cyclones"]) == 5


def test_esri_basemap_is_optional_and_switches_the_stack(monkeypatch):
    events = _fakes(monkeypatch)

    out = _run({"op": "preset", "preset": "weather", "basemap": "esri"})

    assert ("set_map_stack", {"stack": "esri-imagery"}) in events
    assert [event[0] for event in events] == [
        "set_layer_visibility",
        "set_layer_visibility",
        "set_layer_visibility",
        "set_layer_visibility",
        "set_map_stack",
        "get_cyclones",
    ]
    assert out["basemap"] == "esri"


def test_cyclone_summary_sorts_top_five_by_intensity(monkeypatch):
    _fakes(monkeypatch)

    out = _run({"op": "preset", "preset": "weather"})

    assert [row["name"] for row in out["cyclones"]] == [
        "Hurricane Iris",
        "Hurricane Pax",
        "Typhoon Kaito",
        "Hurricane Marco",
        "Cyclone Nisha",
    ]
    first = out["cyclones"][0]
    assert first["category"] == 4
    assert first["wind_kt"] == 130
    assert first["position"] == {"lat": 22.5, "lon": -88.0}
    assert first["movement"] == "WNW at 9 kt"
    assert out["summary"] == (
        "7 active cyclones; strongest Hurricane Iris (Category 4, 130 kt) "
        "at 22.5N 88.0W moving WNW at 9 kt."
    )


def test_place_and_bbox_filter_the_cyclone_summary(monkeypatch):
    events = _fakes(monkeypatch)

    out = _run({"op": "preset", "preset": "weather", "place": "North Atlantic"})
    assert ("get_cyclones", {"area": {"place": "North Atlantic"}}) in events
    assert len(out["cyclones"]) == 5

    events.clear()
    out = _run({"op": "preset", "preset": "weather", "bbox": [-100, 15, -60, 35]})
    assert ("get_cyclones", {"area": {"bbox": [-100, 15, -60, 35]}}) in events
    assert [row["name"] for row in out["cyclones"]] == ["Hurricane Iris", "Hurricane Marco"]
    assert out["summary"].startswith("2 active cyclones; strongest Hurricane Iris")


def test_no_active_cyclones_is_one_clear_sentence(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setattr(
        ext_weather, "mcp_call",
        lambda name, args: {"structuredContent": {"cyclones": [], "count": 0}})

    out = _run({"op": "preset", "preset": "weather"})

    assert out["cyclones"] == []
    assert out["summary"] == "no active cyclones"


def test_unknown_preset_is_refused_without_touching_the_pane_or_mcp(monkeypatch):
    events = _fakes(monkeypatch)
    record = ext_ops.find("godseye_layers", "preset")
    assert record is not None

    out = json.loads(record.handler({"preset": "fire", "basemap": "esri"}))

    assert out["ok"] is False
    assert out["error"] == "bad_args"
    assert events == []

    _record, _forwarded, _extra, error = router.prepare(
        "godseye_layers", {"op": "preset", "preset": "fire"})
    assert error is not None and error["error"] == "bad_args"


def test_preset_ext_op_stays_on_the_eleven_tool_catalog(monkeypatch):
    _fakes(monkeypatch)
    ctx = Ctx()
    hermes_tools.register_tools(ctx)

    assert len(ctx.tools) == 11
    schema = ctx.tools["godseye_layers"]["schema"]["parameters"]
    assert "preset" in schema["properties"]["op"]["enum"]
    assert schema["properties"]["preset"]["enum"] == ["weather"]
    assert schema["properties"]["basemap"]["enum"] == ["esri"]
