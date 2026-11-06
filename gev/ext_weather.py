"""Weather stack preset on godseye_layers, with a cyclone summary."""
from __future__ import annotations

import json
import time

from . import areas, errors
from .ext_ops import ExtOp

_WEATHER_LAYERS = ("wind", "weather-radar", "weather-satellite", "weather-cyclones")
_PRESETS = {"weather": _WEATHER_LAYERS}
_BASEMAPS = {"esri": "esri-imagery"}
_LIVE_RETRY_CODES = {"pane_busy", "timeout"}
_LIVE_RETRIES = 1
_RETRY_DELAY_S = 0.15


def live_call(name: str, args: dict | None = None, session_id: str | None = None,
              fanout: bool = False) -> dict:
    from . import composer
    return composer.live_call(name, args, session_id=session_id, fanout=fanout)


def mcp_call(name: str, args: dict) -> dict:
    from . import composer
    return composer.mcp_call(name, args)


def _fail(error: str, message: str, fix: str | None = None) -> str:
    payload = {"ok": False, "error": error, "message": message}
    if fix:
        payload["fix"] = fix
    return json.dumps(errors.annotate(payload, tool="godseye_layers"), ensure_ascii=False)


def _live_error(result) -> tuple[str | None, dict]:
    """The pane error inside a live result, or ``(None, {})`` when it succeeded.

    A relay/pane answer may carry ``ok:false`` at the top level or wrap the pane
    payload under ``result``; both count as failure. An ``error`` code without an
    ``ok`` key is also a failure (do not fall through and enable the remaining
    layers blind).
    """
    if not isinstance(result, dict):
        return "tool_failed", {"message": "The pane returned no result."}
    sources = [result]
    nested = result.get("result")
    if isinstance(nested, dict) and nested is not result:
        sources.append(nested)
    for source in sources:
        code = source.get("code") or source.get("error")
        if source.get("ok") is False or (code and source.get("ok") is not True):
            return str(code or "tool_failed"), source
    return None, {}


def _live_checked(name: str, args: dict, session_id, label: str) -> tuple[dict, dict | None]:
    """Run one live call with at most one retry for a transient pane state.

    ``no_pane``/``pane_lost``/``relay_unavailable`` are not retried: no amount of
    waiting can enable a layer with no pane. ``pane_busy`` and ``timeout`` get one
    short retry, then the preset stops and reports the structured failure.
    """
    result: dict = {}
    source: dict = {}
    for attempt in range(_LIVE_RETRIES + 1):
        result = live_call(name, dict(args), session_id=session_id)
        source_result = result if isinstance(result, dict) else {}
        code, source = _live_error(result)
        if code is None:
            return source_result, None
        if code in _LIVE_RETRY_CODES and attempt < _LIVE_RETRIES:
            if _RETRY_DELAY_S:
                time.sleep(_RETRY_DELAY_S)
            continue
        source = dict(source)
        source.setdefault("message", f"Could not {label}.")
        return source_result, {"code": code, **source}
    return result if isinstance(result, dict) else {}, {"code": "tool_failed", **source}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _first_number(row: dict, *keys: str):
    for key in keys:
        number = _number(row.get(key))
        if number is not None:
            return number
    return None


def _category(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else value
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text.lower().replace("category", "").strip())
    except ValueError:
        return text
    return int(number) if number.is_integer() else number


def _position(row: dict):
    for key in ("position", "center", "location"):
        value = row.get(key)
        if isinstance(value, dict):
            lat = _number(value.get("lat", value.get("latitude")))
            lon = _number(value.get("lon", value.get("longitude")))
            if lat is not None and lon is not None:
                return {"lat": lat, "lon": lon}
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            lat, lon = _number(value[0]), _number(value[1])
            if lat is not None and lon is not None:
                return {"lat": lat, "lon": lon}
    lat = _first_number(row, "lat", "latitude")
    lon = _first_number(row, "lon", "longitude")
    if lat is not None and lon is not None:
        return {"lat": lat, "lon": lon}
    return None


def _movement(row: dict):
    value = row.get("movement", row.get("motion", row.get("movementDescription")))
    if isinstance(value, dict):
        direction = value.get("direction") or value.get("heading")
        speed = _number(value.get("speedKt", value.get("speed_kt", value.get("speed"))))
        if direction and speed is not None:
            return f"{direction} at {_whole(speed)} kt"
        if direction:
            return str(direction)
        if speed is not None:
            return f"{_whole(speed)} kt"
    elif value not in (None, ""):
        return str(value).strip()
    direction = (row.get("movementDirection") or row.get("movementDir")
                 or row.get("movement_dir") or row.get("direction"))
    speed = _first_number(row, "movementSpeedKt", "movement_speed_kt", "speedKt")
    if direction and speed is not None:
        return f"{direction} at {_whole(speed)} kt"
    if direction:
        return str(direction)
    if speed is not None:
        return f"{_whole(speed)} kt"
    return None


def _whole(value) -> str:
    number = _number(value)
    if number is None:
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _row(source: dict) -> dict:
    name = source.get("name") or source.get("title") or source.get("id") or "unnamed"
    return {
        "name": str(name),
        "category": _category(source.get("category", source.get("classification", source.get("type")))),
        "wind_kt": _first_number(source, "windKt", "wind_kt", "wind", "maxWindKt", "max_wind_kt",
                                 "windSpeedKt", "wind_speed_kt", "intensityKt"),
        "position": _position(source),
        "movement": _movement(source),
    }


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("cyclones", "storms", "rows", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for key in ("data", "result"):
            value = payload.get(key)
            if isinstance(value, (dict, list)):
                found = _rows(value)
                if found:
                    return found
    return []


def _payload(result) -> dict | list:
    if not isinstance(result, dict):
        return result
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)):
        return structured
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item.get("text") or "")
            except (TypeError, ValueError):
                continue
    return result


def _intensity(row: dict) -> float:
    wind = _number(row.get("wind_kt"))
    if wind is not None:
        return wind
    category = row.get("category")
    if isinstance(category, (int, float)) and not isinstance(category, bool):
        return float(category)
    try:
        return float(str(category).lower().replace("category", "").strip())
    except (TypeError, ValueError):
        return -1.0


def _coord(value: float, axis: str) -> str:
    suffix = ("N" if value >= 0 else "S") if axis == "lat" else ("E" if value >= 0 else "W")
    return f"{abs(value):.1f}{suffix}"


def _category_text(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"Category {_whole(value)}"
    return str(value)


def _detail(row: dict) -> str:
    facts = []
    if row.get("category") is not None:
        facts.append(_category_text(row["category"]))
    if _number(row.get("wind_kt")) is not None:
        facts.append(f"{_whole(row['wind_kt'])} kt")
    text = row["name"]
    if facts:
        text += f" ({', '.join(facts)})"
    position = row.get("position")
    if isinstance(position, dict):
        text += f" at {_coord(position['lat'], 'lat')} {_coord(position['lon'], 'lon')}"
    if row.get("movement"):
        text += f" moving {row['movement']}"
    return text


def _summary(rows: list[dict]) -> str:
    if not rows:
        return "no active cyclones"
    noun = "cyclone" if len(rows) == 1 else "cyclones"
    return f"{len(rows)} active {noun}; strongest {_detail(rows[0])}."


def _area(args: dict):
    place = args.get("place")
    bbox = args.get("bbox")
    if place not in (None, "") and bbox is not None:
        return None, None, _fail("bad_args", "preset weather takes place or bbox, not both.",
                                 "Retry with one of place or bbox.")
    if place not in (None, ""):
        if not isinstance(place, str) or not place.strip():
            return None, None, _fail("bad_args", "place must be a non-empty string.",
                                     "Retry with a place name.")
        return {"area": {"place": place.strip()}}, None, None
    if bbox is not None:
        try:
            parsed = areas.interpret_bbox(bbox, prefer="wsen")
        except ValueError as exc:
            return None, None, _fail("bad_args", str(exc),
                                     "Pass bbox as [west, south, east, north].")
        return {"area": {"bbox": areas.to_order(parsed["bbox"], "wsen")}}, parsed["bbox"], None
    return {}, None, None


def _inside(row: dict, box: list[float]) -> bool:
    position = row.get("position")
    if not isinstance(position, dict):
        return False
    south, west, north, east = box
    return south <= position["lat"] <= north and west <= position["lon"] <= east


def weather_preset(args: dict, **kw) -> str:
    args = dict(args or {})
    preset = args.get("preset")
    if preset not in _PRESETS:
        return _fail("bad_args", f"Unknown preset {preset!r}.",
                     "Use preset=weather.")
    basemap = args.get("basemap")
    if basemap is not None and basemap not in _BASEMAPS:
        return _fail("bad_args", f"Unknown basemap {basemap!r}.", "Use basemap=esri.")
    area, box, error = _area(args)
    if error:
        return error
    session_id = kw.get("session_id")
    layers = list(_PRESETS[preset])
    for layer in layers:
        _result, failure = _live_checked(
            "set_layer_visibility", {"layerId": layer, "enabled": True}, session_id,
            f"enable the {layer} layer")
        if failure:
            return _fail(str(failure.get("code") or "tool_failed"),
                         str(failure.get("message") or f"Could not enable {layer}."),
                         failure.get("fix"))
    if basemap is not None:
        _result, failure = _live_checked(
            "set_map_stack", {"stack": _BASEMAPS[basemap]}, session_id,
            f"switch the map stack to {basemap}")
        if failure:
            return _fail(str(failure.get("code") or "tool_failed"),
                         str(failure.get("message") or f"Could not switch the map stack to {basemap}."),
                         failure.get("fix"))
    try:
        result = mcp_call("get_cyclones", area)
    except Exception as exc:
        return _fail("tool_failed", f"get_cyclones failed: {exc}.",
                     "Retry once. If it still fails, call godseye_admin op=doctor.")
    if isinstance(result, dict) and result.get("isError"):
        text = next((item.get("text") for item in result.get("content") or []
                     if isinstance(item, dict) and item.get("type") == "text"), "")
        return _fail("tool_failed", str(text or "get_cyclones failed.")[:500],
                     "Retry once. If it still fails, call godseye_admin op=doctor.")
    rows = [_row(source) for source in _rows(_payload(result))]
    if box is not None:
        rows = [row for row in rows if _inside(row, box)]
    rows.sort(key=_intensity, reverse=True)
    return json.dumps({
        "layers_on": layers,
        "basemap": basemap,
        "cyclones": rows[:5],
        "summary": _summary(rows),
    }, ensure_ascii=False)


EXT_OPS = [
    ExtOp(
        tool="godseye_layers",
        op="preset",
        handler=weather_preset,
        props={
            "preset": {
                "type": "string",
                "enum": ["weather"],
                "required": True,
                "description": "Named layer preset. weather enables wind, radar, clouds, and cyclones.",
            },
            "basemap": {
                "type": "string",
                "enum": ["esri"],
                "description": "Optional basemap switch after the layers: esri.",
            },
            "place": {
                "type": "string",
                "description": "Optional cyclone area filter: a place name.",
            },
            "bbox": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
                "description": "Optional cyclone area filter [west, south, east, north].",
            },
        },
        blurb="Named layer stacks: weather turns on wind, radar, clouds, and cyclones with a cyclone summary.",
    ),
]
