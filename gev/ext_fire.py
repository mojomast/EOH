"""WFIGS wildfire perimeters: watch growth alerts, annotate drawers, and timeline events."""
from __future__ import annotations

import json
import math
import re
import time

from .ext_ops import ExtOp

mcp = None
live = None

_ACRE_KEYS = ("acres", "GISAcres", "DailyAcres", "IncidentAcres", "size_acres", "sizeAcres",
              "map_acres", "calculated_acres", "fire_size")
_CONTAIN_KEYS = ("containment_pct", "PercentContained", "percentContained", "percent_contained",
                 "containment", "contained_pct", "containedPct", "containmentPercent", "pct_contained")
_NAME_KEYS = ("IncidentName", "incident_name", "name", "title", "FireName", "fire_name", "label")
_ID_KEYS = ("UniqueFireIdentifier", "unique_fire_identifier", "id", "fireId", "fire_id",
            "incidentId", "incident_id", "GlobalID", "objectid", "OBJECTID", "IrwinID")
_DISCOVERED_KEYS = ("FireDiscoveryDateTime", "discoveredTime", "discoveryTime", "discovered_date",
                    "discovery_date", "discovered", "DiscoveryDate", "StartDate", "start_date")
_UPDATED_KEYS = ("ModifiedDate", "modifiedTime", "modified_date", "updatedTime", "updated_date",
                 "update_date", "updated", "UpdateDate", "LastUpdate", "last_updated", "lastUpdate")
_LIST_KEYS = ("perimeters", "fires", "items", "results", "data", "features")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

DEFAULT_CENTER_RADIUS_KM = 40.0
_RETRY_ATTEMPTS = 2
_RETRY_BACKOFF_S = 0.5
_TRANSIENT_HINTS = (
    "timeout", "timed out", "timedout", "etimedout", "temporar", "unavailable",
    "rate limit", "too many requests", "connection reset", "connection aborted",
    "connection refused", "econnreset", "econnrefused", "network", "429", "502", "503", "504",
)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _default_center_radius(area):
    """Default a center area's missing radius_km instead of failing BAD_AREA."""
    if not isinstance(area, dict):
        return area
    if area.get("view") or area.get("bbox") is not None or area.get("polygon") is not None:
        return area
    lat = area.get("lat")
    if lat is None:
        lat = area.get("latitude")
    lon = area.get("lon")
    if lon is None:
        lon = area.get("longitude")
    if lat is None or lon is None:
        return area
    if area.get("radius_km") is not None:
        return area
    return {**area, "radius_km": DEFAULT_CENTER_RADIUS_KM}


def _is_transient(exc: Exception | None = None, message=None) -> bool:
    if exc is not None:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        text = f"{type(exc).__name__}: {exc}"
    else:
        text = str(message or "")
    lowered = text.lower()
    return any(hint in lowered for hint in _TRANSIENT_HINTS)


def _fire_error(error, retried: bool = False) -> dict:
    fix = "Retry godseye_annotate or godseye_watch op=fire_perimeters, or narrow the area."
    if retried:
        fix += " The transient failure was retried once."
    return {"status": "ERROR", "items": [], "error": str(error)[:200],
            "code": "fires_failed", "fix": fix}


def _call_mcp(name: str, args: dict) -> dict:
    if mcp is not None:
        return mcp(name, args)
    from .composer import mcp_call
    return mcp_call(name, args)


def _call_live(name: str, args: dict, session_id=None) -> dict:
    if live is not None:
        return live(name, args, session_id=session_id)
    from .composer import live_call
    return live_call(name, args, session_id=session_id)


def _payload(result) -> dict:
    if isinstance(result, list):
        return {"items": result}
    if not isinstance(result, dict):
        return {}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for part in result.get("content") or []:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if not isinstance(text, str):
            continue
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return result


def _rows(data: dict) -> list[dict]:
    for key in _LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return []


def _fields(row: dict) -> list[dict]:
    out = []
    for key in ("properties", "attributes"):
        if isinstance(row.get(key), dict):
            out.append(row[key])
    out.append(row)
    return out


def _first(rows: list[dict], names: tuple[str, ...]):
    lowered = [{str(key).lower(): value for key, value in row.items()} for row in rows]
    for name in names:
        for row in rows:
            value = row.get(name)
            if value not in (None, ""):
                return value
        for row in lowered:
            value = row.get(name.lower())
            if value not in (None, ""):
                return value
    return None


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _day(value) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if value > 1e12 else float(value)
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(seconds))
        except (OverflowError, OSError, ValueError):
            return None
    match = _DATE.match(str(value).strip())
    return match.group(0) if match else None


def _coords_point(coords) -> tuple[float, float] | None:
    points: list[tuple[float, float]] = []

    def walk(value) -> None:
        if not isinstance(value, list):
            return
        if len(value) >= 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                                   for item in value[:2]):
            points.append((float(value[1]), float(value[0])))
            return
        for item in value:
            walk(item)

    walk(coords)
    if not points:
        return None
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def _point(row: dict) -> tuple[float | None, float | None]:
    fields = _fields(row)
    lat = _number(_first(fields, ("lat", "latitude", "Lat", "Latitude")))
    lon = _number(_first(fields, ("lon", "lng", "longitude", "Lon", "Longitude")))
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    for key in ("point", "centroid", "center"):
        value = _first(fields, (key,))
        if isinstance(value, dict):
            plat = _number(value.get("lat", value.get("latitude")))
            plon = _number(value.get("lon", value.get("lng", value.get("longitude"))))
            if plat is not None and plon is not None:
                return plat, plon
    bbox = _first(fields, ("bbox", "bounds"))
    if isinstance(bbox, list) and len(bbox) >= 4:
        nums = [_number(item) for item in bbox[:4]]
        if all(num is not None for num in nums):
            return (nums[1] + nums[3]) / 2, (nums[0] + nums[2]) / 2
    for key in ("geometry", "the_geom", "shape"):
        value = _first((row,), (key,))
        if isinstance(value, dict):
            point = _coords_point(value.get("coordinates"))
            if point:
                return point
    return None, None


def _http_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    url = value.strip()
    if url.startswith(("https://", "http://")) and len(url) < 400 and " " not in url:
        return url
    return None


def _inciweb(row: dict) -> str | None:
    for part in _fields(row):
        for key, value in part.items():
            if "inciweb" in str(key).lower():
                url = _http_url(value)
                if url:
                    return url
        links = part.get("links")
        if isinstance(links, list):
            for link in links:
                if not isinstance(link, dict):
                    continue
                url = _http_url(link.get("url"))
                if url and "inciweb" in (str(link.get("label") or "") + url).lower():
                    return url
    return None


def _name(row: dict) -> str:
    value = _first(_fields(row), _NAME_KEYS)
    return str(value).strip()[:120] if value not in (None, "") else "Wildfire perimeter"


def _identifier(row: dict) -> str:
    value = _first(_fields(row), _ID_KEYS)
    if value not in (None, ""):
        text = str(value).strip()
        return text if text.startswith("wfigs:") else f"wfigs:{text}"
    name = _name(row)
    lat, lon = _point(row)
    return f"wfigs:{name}:{lat:.4f},{lon:.4f}" if lat is not None and lon is not None else f"wfigs:{name}"


def _acres(row: dict) -> float | None:
    value = _first(_fields(row), _ACRE_KEYS)
    if value is None:
        for part in _fields(row):
            for key, candidate in part.items():
                if "acre" in str(key).lower():
                    value = _number(candidate)
                    if value is not None:
                        return value
    return _number(value)


def _containment(row: dict) -> float | None:
    return _number(_first(_fields(row), _CONTAIN_KEYS))


def perimeter_drawer(record: dict) -> dict:
    """Annotate pin and drawer fields for one WFIGS perimeter record."""
    row = record if isinstance(record, dict) else {}
    name = _name(row)
    lat, lon = _point(row)
    acres = _acres(row)
    contained = _containment(row)
    cause = _first(_fields(row), ("FireCause", "fire_cause", "cause"))
    personnel = _number(_first(_fields(row), ("TotalPersonnel", "total_personnel", "personnel")))
    discovered = _day(_first(_fields(row), _DISCOVERED_KEYS))
    updated = _day(_first(_fields(row), _UPDATED_KEYS))
    url = _inciweb(row)

    lines = [f"**Fire:** {name}"]
    if acres is not None:
        lines.append(f"**Acres:** {acres:,.0f}")
    if contained is not None:
        lines.append(f"**Contained:** {contained:g}%")
    if cause not in (None, ""):
        lines.append(f"**Cause:** {cause}")
    if personnel is not None:
        lines.append(f"**Personnel:** {personnel:,.0f}")
    if discovered:
        lines.append(f"**Discovered:** {discovered}")
    if updated:
        lines.append(f"**Updated:** {updated}")
    if url:
        lines.append(f"[InciWeb]({url})")

    annotation = {
        "type": "pin",
        "latitude": lat,
        "longitude": lon,
        "label": name,
        "title": name,
        "description_md": "\n".join(lines),
        "links": [{"label": "InciWeb", "url": url}] if url else [],
        "tags": {"category": "fire_perimeters"},
    }
    return annotation


def _item(row: dict, grow_pct: float, previous: dict) -> dict:
    annotation = perimeter_drawer(row)
    discovered = _day(_first(_fields(row), _DISCOVERED_KEYS))
    updated = _day(_first(_fields(row), _UPDATED_KEYS))
    stamp = updated or discovered
    acres = _acres(row)
    item = {
        "id": _identifier(row),
        "lat": annotation["latitude"],
        "lon": annotation["longitude"],
        "title": annotation["title"],
        "time": f"{stamp}T00:00:00Z" if stamp else None,
        "description_md": annotation["description_md"],
        "links": annotation["links"],
        "tags": annotation["tags"],
        "acres": acres,
        "containment_pct": _containment(row),
        "discovered": discovered,
        "updated": updated,
    }
    prior = _number(previous.get(item["id"]))
    if acres is not None and prior is not None:
        if prior <= 0:
            changed = acres > 0
        else:
            changed = (acres - prior) / prior * 100.0 >= grow_pct
        if changed:
            item["changed"] = True
            if prior > 0:
                item["grew_pct"] = round((acres - prior) / prior * 100.0, 1)
    return item


def fetch_perimeters(area: dict, conditions: dict | None = None) -> dict:
    cond = conditions or {}
    try:
        s, w, n, e = area["bbox"]
    except (KeyError, TypeError, ValueError):
        return {"status": "ERROR", "items": [], "error": "area needs a bbox for fire perimeters"}
    try:
        limit = int(cond.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(200, limit))
    grow_pct = _number(cond.get("grow_pct"))
    grow_pct = 10.0 if grow_pct is None else grow_pct
    previous = cond.get("_previous_acres")
    previous = previous if isinstance(previous, dict) else {}
    last_error = "get_fire_perimeters failed"
    for attempt in range(_RETRY_ATTEMPTS):
        retry_left = attempt + 1 < _RETRY_ATTEMPTS
        try:
            result = _call_mcp("get_fire_perimeters", {"area": {"bbox": [w, s, e, n]}, "limit": limit})
        except Exception as exc:
            last_error = f"get_fire_perimeters failed: {type(exc).__name__}: {str(exc)[:160]}"
            if retry_left and _is_transient(exc=exc):
                _sleep(_RETRY_BACKOFF_S)
                continue
            return _fire_error(last_error, retried=attempt > 0)
        data = _payload(result)
        if isinstance(data.get("ok"), bool) and not data["ok"]:
            detail = " ".join(str(data.get(key) or "") for key in ("code", "error", "message"))
            last_error = str(data.get("error") or data.get("message") or last_error)[:200]
            if retry_left and _is_transient(message=detail):
                _sleep(_RETRY_BACKOFF_S)
                continue
            return _fire_error(last_error, retried=attempt > 0)
        rows = _rows(data)
        return {"status": "OK", "items": [_item(row, grow_pct, previous) for row in rows]}
    return _fire_error(last_error, retried=_RETRY_ATTEMPTS > 1)


def _view_fn(session_id):
    def view():
        return _call_live("get_current_view_state", {}, session_id=session_id)
    return view


def watch_fire_perimeters(args: dict, **kw) -> str:
    from . import watch
    grow_pct = _number(args.get("grow_pct"))
    grow_pct = 10.0 if grow_pct is None else grow_pct
    conditions = {"fire_perimeters": {"grow_pct": grow_pct}}
    limit = _number(args.get("limit"))
    if limit is not None:
        conditions["fire_perimeters"]["limit"] = int(limit)
    store = watch.store()
    out = store.create({
        "area": _default_center_radius(args.get("area")),
        "feeds": ["fire_perimeters"],
        "name": args.get("name") or "Fire perimeters",
        "conditions": conditions,
        "interval_s": args.get("interval_s"),
        "baseline": args.get("baseline", True),
        "pin": args.get("pin", True),
        "deliver": args.get("deliver") or "queue",
    }, session_id=kw.get("session_id"), view_fn=_view_fn(kw.get("session_id")))
    if out.get("ok") and args.get("run_now", True) is not False:
        out["first_check"] = store.check(out["watch"]["id"])
    return json.dumps(out, ensure_ascii=False)


def annotate_fire_perimeters(args: dict, **kw) -> str:
    from . import watch
    try:
        area = watch.normalize_area(_default_center_radius(args.get("area") or {}))
    except ValueError as exc:
        return json.dumps({"ok": False, "error": "BAD_AREA", "message": str(exc)}, ensure_ascii=False)
    conditions = {}
    limit = _number(args.get("limit"))
    if limit is not None:
        conditions["limit"] = int(limit)
    result = fetch_perimeters(area, conditions)
    if result.get("status") != "OK":
        body = {"ok": False, "error": result.get("error") or "fire perimeters unavailable",
                "code": result.get("code") or "fires_failed"}
        if result.get("fix"):
            body["fix"] = result["fix"]
        return json.dumps(body, ensure_ascii=False)
    annotations = [perimeter_drawer(item) for item in result["items"]
                   if item.get("lat") is not None and item.get("lon") is not None]
    intent = args.get("intent") or "WFIGS fire perimeters"
    try:
        pane = _call_live("annotate_map", {"annotations": annotations, "intent": intent},
                          session_id=kw.get("session_id"))
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"annotate_map failed: {type(exc).__name__}",
                           "count": len(annotations), "annotations": annotations}, ensure_ascii=False)
    ok = bool(pane.get("ok", True)) if isinstance(pane, dict) else False
    return json.dumps({"ok": ok, "count": len(annotations), "annotations": annotations, "pane": pane},
                      ensure_ascii=False)


_AREA = {
    "type": "object",
    "description": ("Place name, bbox [south, west, north, east], lat/lon with radius_km "
                    "(default 40 km when omitted), or view=true."),
    "properties": {
        "place": {"type": "string"},
        "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
        "lat": {"type": "number"},
        "lon": {"type": "number"},
        "radius_km": {"type": "number", "description": "Center radius in kilometers. Default 40."},
        "view": {"type": "boolean"},
    },
    "required": True,
}

WATCH_PROPS = {
    "area": _AREA,
    "name": {"type": "string", "description": "Watch label."},
    "grow_pct": {"type": "number", "description": "Alert when acres grow by at least this percent. Default 10."},
    "interval_s": {"type": "integer", "description": "Poll interval in seconds."},
    "baseline": {"type": "boolean", "description": "false alerts on perimeters already present on the first run."},
    "pin": {"type": "boolean", "description": "Pin each new or grown perimeter. Default true."},
    "deliver": {"type": "string", "enum": ["queue", "inject"]},
    "run_now": {"type": "boolean", "description": "Run the first baseline check now. Default true."},
    "limit": {"type": "integer", "description": "Most WFIGS rows to fetch (1-200)."},
}

ANNOTATE_PROPS = {
    "area": _AREA,
    "intent": {"type": "string", "description": "User's request, used for the pin drawers."},
    "limit": {"type": "integer", "description": "Most WFIGS rows to draw (1-200)."},
}

EXT_OPS = [
    ExtOp(tool="godseye_watch", op="fire_perimeters", handler=watch_fire_perimeters,
          props=WATCH_PROPS, blurb="Watch WFIGS fire perimeters; alert on new or grown fires. "
          "Center areas without radius_km default to 40 km."),
    ExtOp(tool="godseye_annotate", op="fire_perimeters", handler=annotate_fire_perimeters,
          props=ANNOTATE_PROPS, blurb="Pin WFIGS fire perimeters with acres and containment drawers. "
          "Center areas without radius_km default to 40 km."),
]
