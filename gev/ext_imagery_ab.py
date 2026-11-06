"""Before-and-after recent imagery on godseye_nav.

The MCP ``get_recent_imagery`` catalog supplies the days. This op chooses the
earliest and latest days under ``max_cloud`` and hands the pane two
``find_imagery`` calls, one per day. The MCP and live calls go through
``mcp`` and ``live`` so tests can replace them with fixtures.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone

from . import areas
from .ext_ops import ExtOp

HERE = {"here", "this area", "the view", "current view", "this view"}
DATE_KEYS = ("date", "day", "acquired", "acquisition_date", "acquired_at",
             "datetime", "timestamp", "start")
CLOUD_KEYS = ("cloud", "cloud_pct", "cloudPct", "cloud_cover", "cloudCover",
              "cloud_cover_pct", "cloudPercent", "clouds")
ID_KEYS = ("id", "key", "scene_id", "sceneId", "granule_id", "granule",
           "product_id", "image_id", "identifier", "name")
PRODUCT_KEYS = ("product", "sensor", "source", "platform", "dataset")
DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")


_MISSING = object()


def __getattr__(name):
    if name != "EXT_OPS":
        raise AttributeError(name)
    if sys.modules.get("gev.ext_broken", _MISSING) is None:
        return []
    return _EXT_OPS


def mcp(name: str, args: dict):
    """Default MCP hook: the same handler godseye_feeds op=imagery uses."""
    from . import hermes_tools

    text = hermes_tools.make_handler(name)(dict(args or {}))
    return _object(text)


def live(name: str, args: dict, **kw):
    """Default live hook: the same handler godseye_feeds op=imagery_live uses."""
    from . import hermes_tools

    text = hermes_tools.make_live_handler(name)(dict(args or {}), **kw)
    return _object(text)


def now() -> date:
    return datetime.now(timezone.utc).date()


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            start, end = value.find("{"), value.rfind("}")
            if start < 0 or end <= start:
                return {}
            try:
                parsed = json.loads(value[start:end + 1])
            except (TypeError, ValueError):
                return {}
        return parsed if isinstance(parsed, dict) else {"items": parsed}
    return {}


def _fail(error: str, message: str, fix: str, **extra) -> str:
    body = {"ok": False, "error": error, "code": error, "message": message, "fix": fix}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _day(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e12:
            stamp /= 1000.0
        try:
            return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        match = DAY.match(value.strip())
        if match:
            return match.group(1)
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return None
    return None


def _cloud(value):
    if isinstance(value, dict):
        for key in ("max", "value", "mean", "avg", "pct"):
            if key in value:
                number = _number(value.get(key))
                if number is not None:
                    return number
        values = [_number(item) for item in value.values()]
        values = [item for item in values if item is not None]
        return max(values) if values else None
    return _number(value)


def _first(mapping: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        for name, value in lowered.items():
            if name == key.lower() and value is not None:
                return value
    return None


def _candidate(value: dict):
    day = _day(_first(value, DATE_KEYS))
    if day is None and isinstance(value.get("timeRange"), dict):
        day = _day(_first(value["timeRange"], ("start", "from", "day")))
    if day is None:
        return None
    cloud = _cloud(_first(value, CLOUD_KEYS))
    ident = _first(value, ID_KEYS)
    product = _first(value, PRODUCT_KEYS)
    if cloud is None and ident is None and product is None:
        return None
    lat = _number(_first(value, ("lat", "latitude")))
    lon = _number(_first(value, ("lon", "lng", "longitude")))
    if not isinstance(ident, (str, int)) or isinstance(ident, bool) or not str(ident):
        ident = f"{product or 'imagery'}:{day}"
    return {
        "date": day,
        "cloud": cloud,
        "id": str(ident),
        "product": str(product or "imagery"),
        "lat": lat,
        "lon": lon,
    }


def _collect(value, found: list[dict], seen: set[tuple]):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[":
            _collect(_object(stripped), found, seen)
        return
    if isinstance(value, list):
        for item in value:
            _collect(item, found, seen)
        return
    if not isinstance(value, dict):
        return
    cand = _candidate(value)
    if cand is not None:
        key = (cand["date"], cand["cloud"], cand["id"])
        if key not in seen:
            seen.add(key)
            found.append(cand)
    for key, item in value.items():
        if str(key).lower() in {"bbox", "geometry", "coordinates"}:
            continue
        _collect(item, found, seen)


def _days(value) -> list[dict]:
    found: list[dict] = []
    _collect(_object(value), found, set())
    return found


def _in_box(candidate: dict, box: list[float]) -> bool:
    lat, lon = candidate.get("lat"), candidate.get("lon")
    if lat is None or lon is None:
        return True
    west, south, east, north = box
    return west <= lon <= east and south <= lat <= north


def _best_available(candidates: list[dict]):
    if not candidates:
        return None
    known = [item for item in candidates if item["cloud"] is not None]
    if not known:
        return max(candidates, key=lambda item: (item["date"], item["id"]))
    lowest = min(item["cloud"] for item in known)
    tied = [item for item in known if item["cloud"] == lowest]
    return max(tied, key=lambda item: (item["date"], item["id"]))


def _pick(pool: list[dict], role: str, max_cloud: float):
    good = [item for item in pool if item["cloud"] is not None and item["cloud"] <= max_cloud]
    if not good:
        return None
    by_day: dict[str, dict] = {}
    for item in good:
        current = by_day.get(item["date"])
        if current is None or (item["cloud"], item["id"]) < (current["cloud"], current["id"]):
            by_day[item["date"]] = item
    days = sorted(by_day)
    return by_day[days[0] if role == "before" else days[-1]]


def _bbox_around(lat: float, lon: float, radius_km: float) -> list[float]:
    lat_span = radius_km / 111.32
    lon_span = radius_km / (111.32 * max(0.01, math.cos(math.radians(lat))))
    return [
        max(-180.0, lon - lon_span),
        max(-90.0, lat - lat_span),
        min(180.0, lon + lon_span),
        min(90.0, lat + lat_span),
    ]


def _find_bbox(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == "bbox":
                parsed = _bbox_value(item)
                if parsed is not None:
                    return parsed
            parsed = _find_bbox(item)
            if parsed is not None:
                return parsed
    elif isinstance(value, list):
        parsed = _bbox_value(value)
        if parsed is not None:
            return parsed
        for item in value:
            parsed = _find_bbox(item)
            if parsed is not None:
                return parsed
    return None


def _bbox_value(value):
    if isinstance(value, dict) and all(name in value for name in ("west", "south", "east", "north")):
        try:
            data = areas.interpret_bbox(value, prefer="wsen")
        except (TypeError, ValueError):
            return None
        south, west, north, east = data["bbox"]
        return [west, south, east, north]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        data = areas.interpret_bbox(list(value), prefer="wsen")
    except (TypeError, ValueError):
        return None
    south, west, north, east = data["bbox"]
    return [west, south, east, north]


def _find_center(value):
    if isinstance(value, dict):
        camera = value.get("camera") if isinstance(value.get("camera"), dict) else {}
        for source in (camera, value):
            lat = _number(_first(source, ("latitude", "lat")))
            lon = _number(_first(source, ("longitude", "lng", "lon")))
            if lat is not None and lon is not None:
                return lat, lon
        for item in value.values():
            found = _find_center(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_center(item)
            if found is not None:
                return found
    return None


def _span_km(box: list[float]) -> float:
    west, south, east, north = box
    mid = (south + north) / 2.0
    width = abs(east - west) * 111.32 * max(0.01, math.cos(math.radians(mid)))
    height = abs(north - south) * 111.32
    return max(width, height)


def _call_live(name: str, args: dict, session_id=None):
    try:
        return live(name, args, session_id=session_id)
    except TypeError:
        return live(name, args)


def _view():
    try:
        state = _call_live("get_current_view_state", {})
    except Exception as exc:  # the pane may be closed; the error is the answer
        return None, None, f"The view is unavailable: {exc}."
    body = _object(state)
    if body.get("ok") is False:
        return None, None, str(body.get("message") or body.get("error") or "The pane is not open.")
    box = _find_bbox(body)
    if box is not None:
        return {"bbox": box}, box, None
    center = _find_center(body)
    if center is None:
        return None, None, "The view has no camera position."
    lat, lon = center
    box = _bbox_around(lat, lon, 10.0)
    return {"lat": lat, "lon": lon, "radius_km": 10.0}, box, None


def _date_arg(value):
    if value is None or value == "":
        return None, None
    if not isinstance(value, str):
        return None, "Dates must be YYYY-MM-DD."
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date().isoformat(), None
    except ValueError:
        return None, "Dates must be YYYY-MM-DD."


def _payload(args: dict, session_id=None):
    place = args.get("place")
    bbox = args.get("bbox")
    if place is not None and not isinstance(place, str):
        return None, None, None, _fail(
            "bad_args", "place must be a string.", "Pass place as a name or 'here'.")
    place = place.strip() if isinstance(place, str) else None
    if place and bbox is not None:
        return None, None, None, _fail(
            "bad_args", "Pass place or bbox, not both.", "Choose one area input.")
    here = not place and bbox is None
    if place and place.lower() in HERE:
        here = True
    if bbox is not None:
        try:
            parsed = areas.interpret_bbox(bbox, prefer="wsen")
        except (TypeError, ValueError) as exc:
            return None, None, None, _fail(
                "bad_args", str(exc), "Pass bbox as [west, south, east, north].")
        south, west, north, east = parsed["bbox"]
        box = [west, south, east, north]
        return {"area": {"bbox": box}}, box, None, None
    if here:
        area, box, error = _view()
        if error:
            return None, None, None, _fail(
                "here_unavailable", error, "Open the EOH pane or pass place or bbox.")
        return {"area": area}, box, None, None
    return {"area": {"place": place}}, None, place, None


def handler(args: dict, **kw) -> str:
    args = dict(args or {})
    session_id = kw.get("session_id")
    try:
        max_cloud = _number(args.get("max_cloud", 20))
        window_days = _number(args.get("window_days", 30))
        before_arg, before_error = _date_arg(args.get("before"))
        after_arg, after_error = _date_arg(args.get("after"))
    except Exception as exc:
        return _fail("bad_args", str(exc), "Pass valid dates and numbers.")
    if before_error or after_error:
        return _fail("bad_args", before_error or after_error, "Pass dates as YYYY-MM-DD.")
    if max_cloud is None or not 0 <= max_cloud <= 100:
        return _fail("bad_args", "max_cloud must be 0-100.", "Pass max_cloud between 0 and 100.")
    if window_days is None or int(window_days) < 1:
        return _fail("bad_args", "window_days must be at least 1.", "Pass window_days as a positive integer.")
    window_days = int(window_days)

    mcp_args, box, _place, error = _payload(args, session_id)
    if error:
        return error
    try:
        result = mcp("get_recent_imagery", mcp_args)
    except Exception as exc:
        return _fail("mcp_failed", f"Recent imagery failed: {exc}.",
                     "Check that the God's Eye View app and its MCP server are running.")
    if box is None:
        box = _find_bbox(_object(result))
    candidates = _days(result)
    if box is not None:
        candidates = [item for item in candidates if _in_box(item, box)]
    if not candidates:
        return _fail("no_imagery", "No recent imagery days cover that area.",
                     "Widen the area or pass a different place.")

    first_day = (now() - timedelta(days=window_days)).isoformat()
    window = [item for item in candidates if item["date"] >= first_day]
    if not window:
        newest = max(item["date"] for item in candidates)
        first_day = (date.fromisoformat(newest) - timedelta(days=window_days)).isoformat()
        window = [item for item in candidates if item["date"] >= first_day]

    if before_arg:
        exact = [item for item in candidates if item["date"] == before_arg]
        if exact:
            before_pool = exact
        else:
            earlier = [item for item in candidates if item["date"] <= before_arg]
            latest = max((item["date"] for item in earlier), default=None)
            before_pool = [item for item in earlier if item["date"] == latest]
    else:
        before_pool = window

    if after_arg:
        exact = [item for item in candidates if item["date"] == after_arg]
        if exact:
            after_pool = exact
        else:
            later = [item for item in candidates if item["date"] >= after_arg]
            earliest = min((item["date"] for item in later), default=None)
            after_pool = [item for item in later if item["date"] == earliest]
    else:
        after_pool = window

    before = _pick(before_pool, "before", max_cloud)
    after = _pick(after_pool, "after", max_cloud)
    if before is None or after is None:
        best = _best_available(candidates)
        best_text = "none"
        if best is not None:
            cloud = "unknown" if best["cloud"] is None else f"{best['cloud']}%"
            best_text = f"{best['date']} ({cloud} cloud)"
        return _fail(
            "no_clear_day",
            f"No day at or under {max_cloud:g}% cloud. Best available: {best_text}.",
            "Raise max_cloud or widen window_days.",
            max_cloud=max_cloud,
            best_available=({key: best[key] for key in ("date", "cloud", "id")}
                            if best is not None else None),
        )

    actions = []
    for day in (before, after):
        actions.append({
            "name": "find_imagery",
            "args": {
                "dateRange": {"from": day["date"], "to": day["date"]},
                "maxCloudPct": max_cloud,
                "source": "any",
            },
        })
    shown = []
    for action in actions:
        try:
            shown.append(_call_live(action["name"], dict(action["args"]), session_id))
        except Exception as exc:
            shown.append({"ok": False, "error": f"find_imagery failed: {exc}"})

    return json.dumps({
        "ok": True,
        "op": "imagery_ab",
        "before": {key: before[key] for key in ("date", "cloud", "id")},
        "after": {key: after[key] for key in ("date", "cloud", "id")},
        "mode": "swipe" if before["date"] != after["date"] else "ab",
        "drape_recommended": box is not None and _span_km(box) < 50.0,
        "actions": actions,
        "shown": shown,
    }, ensure_ascii=False)


_EXT_OPS = [
    ExtOp(
        tool="godseye_nav",
        op="imagery_ab",
        handler=handler,
        props={
            "place": {
                "type": "string",
                "description": "Place to compare, or 'here' for the open pane view.",
            },
            "bbox": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
                "description": "Area as [west, south, east, north] degrees.",
            },
            "before": {
                "type": "string",
                "description": "Before image date, YYYY-MM-DD. Omit to pick automatically.",
            },
            "after": {
                "type": "string",
                "description": "After image date, YYYY-MM-DD. Omit to pick automatically.",
            },
            "window_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "default": 30,
                "description": "Days back to search when dates are omitted.",
            },
            "max_cloud": {
                "type": "number",
                "minimum": 0,
                "maximum": 100,
                "default": 20,
                "description": "Highest cloud cover percent that still counts as clear.",
            },
        },
        blurb="Before-and-after recent imagery: earliest and latest clear days, with the pane calls.",
    ),
]
