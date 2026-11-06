"""Fire-checking playbook: FIRMS hotspots, the clearest recent image, then before/after.

The sequence is the existing feeds and imagery ops. It does not create a watch
or drop pins, so running it again with the same area repeats the same lookups.

1. ``godseye_feeds`` ``op=fires`` — NASA FIRMS hotspots, strongest radiative power.
2. ``godseye_feeds`` ``op=imagery`` — recent days; the clearest is the lowest cloud, then the latest date.
3. ``godseye_nav`` ``op=imagery_ab`` — earliest and latest clear days, shown on the pane.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

from . import areas
from .ext_ops import ExtOp

HERE = {"here", "this area", "the view", "current view", "this view"}
SEQUENCE = (
    "godseye_feeds op=fires",
    "godseye_feeds op=imagery",
    "godseye_nav op=imagery_ab",
)


def fires(args: dict):
    from .hermes_tools import make_handler
    return make_handler("get_active_fires")(dict(args))


def imagery(args: dict):
    from .hermes_tools import make_handler
    return make_handler("get_recent_imagery")(dict(args))


def imagery_ab(args: dict, session_id=None):
    from .ext_imagery_ab import handler
    return handler(dict(args), session_id=session_id)


def camera(session_id):
    from .router import camera as read
    return read(session_id)


def now() -> date:
    return datetime.now(timezone.utc).date()


def _fail(error: str, message: str, fix: str, **extra) -> str:
    body = {"ok": False, "error": error, "code": error, "message": message, "fix": fix, "sequence": list(SEQUENCE)}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {"items": parsed}
    return {}


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _hotspot_row(row: dict) -> dict | None:
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    lat = _num(row.get("lat", row.get("latitude", props.get("lat", props.get("latitude")))))
    lon = _num(row.get("lon", row.get("lng", row.get("longitude", props.get("lon", props.get("longitude"))))))
    geometry = row.get("geometry") if isinstance(row.get("geometry"), dict) else {}
    coords = geometry.get("coordinates")
    if (lat is None or lon is None) and isinstance(coords, list) and len(coords) >= 2:
        if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in coords[:2]):
            lon = _num(coords[0])
            lat = _num(coords[1])
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    frp = _num(row.get("frp", props.get("frp", props.get("FRP"))))
    return {"lat": lat, "lon": lon, "frp": frp}


def _hotspots(value) -> list[dict]:
    found = []

    def walk(item):
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, dict):
            return
        row = _hotspot_row(item)
        if row is not None and (row["frp"] is not None or "frp" in item or "properties" in item or "latitude" in item or "lat" in item):
            found.append(row)
            return
        for child in item.values():
            if isinstance(child, (dict, list)):
                walk(child)

    walk(_object(value))
    unique = []
    seen = set()
    for row in found:
        key = (round(row["lat"], 4), round(row["lon"], 4), row["frp"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    unique.sort(key=lambda row: (-(row["frp"] if row["frp"] is not None else -1), row["lat"], row["lon"]))
    return unique


def _box(lat: float, lon: float, km: float = 20.0) -> list[float]:
    dlat = km / 111.32
    dlon = km / (111.32 * max(0.2, math.cos(math.radians(lat))))
    return [
        max(-180.0, lon - dlon),
        max(-90.0, lat - dlat),
        min(180.0, lon + dlon),
        min(90.0, lat + dlat),
    ]


def _clearest(days: list[dict], max_cloud: float, window_days: int, today: date):
    if not days:
        return None
    first = (today - timedelta(days=window_days)).isoformat()
    window = [item for item in days if item.get("date", "") >= first] or list(days)
    best = None
    for item in window:
        cloud = item.get("cloud")
        rank = cloud if isinstance(cloud, (int, float)) else 101
        if best is None:
            best = item
            continue
        best_rank = best.get("cloud") if isinstance(best.get("cloud"), (int, float)) else 101
        if rank < best_rank or (rank == best_rank and item.get("date", "") > best.get("date", "")):
            best = item
    if best is None:
        return None
    if isinstance(best.get("cloud"), (int, float)) and best["cloud"] > max_cloud:
        best = dict(best)
        best["within_max_cloud"] = False
    else:
        best = dict(best)
        best["within_max_cloud"] = True
    return {key: best.get(key) for key in ("date", "cloud", "id", "within_max_cloud")}


def _area(args: dict, session_id):
    place = args.get("place")
    bbox = args.get("bbox")
    lat, lon = _num(args.get("lat")), _num(args.get("lon"))
    if isinstance(place, str):
        place = place.strip()
    if place and bbox is not None:
        return None, _fail("bad_args", "Pass place or bbox, not both.", "Choose one area.")
    if isinstance(place, str) and place.lower() in HERE:
        place = ""
    if bbox is not None:
        try:
            parsed = areas.interpret_bbox(bbox, prefer="wsen")
        except (TypeError, ValueError) as exc:
            return None, _fail("bad_args", str(exc), "Pass bbox as [west, south, east, north].")
        south, west, north, east = parsed["bbox"]
        return {"bbox": [west, south, east, north]}, None
    if place:
        return {"place": place[:200]}, None
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        radius = _num(args.get("radius_km")) or 25
        return {"lat": lat, "lon": lon, "radius_km": radius}, None
    point, err = camera(session_id)
    if err:
        message = err.get("message") if isinstance(err, dict) else "The pane has no camera."
        return None, _fail("here_unavailable", str(message), "Pass place, bbox, or lat and lon.")
    return {"lat": point["lat"], "lon": point["lon"], "radius_km": 25}, None


def _tool_error(body: dict) -> str | None:
    if not isinstance(body, dict):
        return "The feed returned nothing."
    if body.get("ok") is False or body.get("isError") or body.get("code") in {"tool_failed", "bad_args"}:
        return str(body.get("message") or body.get("error") or "The feed failed.")
    if isinstance(body.get("error"), str) and body.get("error"):
        return str(body["error"])
    return None


def _summary(count: int, hotspot: dict | None, clearest: dict | None, compared: dict | None) -> str:
    if not hotspot:
        return "No FIRMS hotspots in that area."
    frp = hotspot.get("frp")
    power = f"{frp:g} MW" if isinstance(frp, (int, float)) else "FRP unknown"
    parts = [f"FIRMS {count} hotspot(s); strongest {power} at {hotspot['lat']:.3f},{hotspot['lon']:.3f}."]
    if clearest and clearest.get("date"):
        cloud = "unknown" if clearest.get("cloud") is None else f"{clearest['cloud']:g}%"
        parts.append(f"Clearest {clearest['date']} ({cloud} cloud).")
    if compared and compared.get("ok"):
        before = (compared.get("before") or {}).get("date")
        after = (compared.get("after") or {}).get("date")
        parts.append(f"Before {before} / after {after} ({compared.get('mode') or 'ab'}).")
    elif compared and compared.get("error"):
        parts.append(str(compared.get("message") or compared.get("error")))
    return " ".join(parts)


def fire_check(args: dict, **kw) -> str:
    args = dict(args or {})
    session_id = kw.get("session_id")
    try:
        max_cloud = _num(args.get("max_cloud", 20))
        window_days = _num(args.get("window_days", 30))
    except Exception:
        max_cloud, window_days = None, None
    if max_cloud is None or not 0 <= max_cloud <= 100:
        return _fail("bad_args", "max_cloud must be 0-100.", "Pass max_cloud between 0 and 100.")
    if window_days is None or int(window_days) < 1 or int(window_days) > 365:
        return _fail("bad_args", "window_days must be 1-365.", "Pass window_days as a positive integer.")
    window_days = int(window_days)
    area, error = _area(args, session_id)
    if error:
        return error

    try:
        raw_fires = fires({"area": area, "detail": "full"})
    except Exception as exc:
        return _fail("fires_failed", f"FIRMS failed: {exc}.", "Retry godseye_feeds op=fires. A missing FIRMS key is reported by op=doctor.")
    fire_body = _object(raw_fires)
    message = _tool_error(fire_body)
    if message and not _hotspots(fire_body):
        return _fail("fires_failed", message, "Retry godseye_feeds op=fires, or narrow the area.")
    rows = _hotspots(fire_body)
    if not rows:
        return json.dumps({
            "ok": True, "op": "fire_check", "sequence": [SEQUENCE[0]],
            "hotspots": 0, "summary": "No FIRMS hotspots in that area.",
        }, ensure_ascii=False)
    hotspot = {key: rows[0][key] for key in ("lat", "lon", "frp")}
    focus = {"bbox": [round(n, 4) for n in _box(hotspot["lat"], hotspot["lon"])]}

    try:
        raw_imagery = imagery({"area": focus, "detail": "full"})
    except Exception as exc:
        return _fail("imagery_failed", f"Recent imagery failed: {exc}.",
                     "Retry godseye_feeds op=imagery.", hotspot=hotspot, hotspots=len(rows))
    image_body = _object(raw_imagery)
    message = _tool_error(image_body)
    from .ext_imagery_ab import _days
    days = _days(image_body)
    if message and not days:
        return _fail("imagery_failed", message, "Retry godseye_feeds op=imagery.",
                     hotspot=hotspot, hotspots=len(rows))
    clearest = _clearest(days, max_cloud, window_days, now())
    if clearest is None:
        return _fail("no_imagery", "No recent imagery days cover that hotspot.",
                     "Widen window_days or pass a different place.",
                     hotspot=hotspot, hotspots=len(rows))

    ab_args = {"bbox": focus["bbox"], "window_days": window_days, "max_cloud": max_cloud}
    cloud = clearest.get("cloud")
    if isinstance(cloud, (int, float)) and not isinstance(cloud, bool) and cloud <= max_cloud and clearest.get("date"):
        ab_args["after"] = clearest["date"]
    try:
        raw_ab = imagery_ab(ab_args, session_id=session_id)
    except Exception as exc:
        return _fail("imagery_ab_failed", f"Before/after failed: {exc}.",
                     "Retry godseye_nav op=imagery_ab.", hotspot=hotspot, clearest=clearest)
    compared = _object(raw_ab)
    brief = {
        "ok": bool(compared.get("ok")),
        "before": compared.get("before"),
        "after": compared.get("after"),
        "mode": compared.get("mode"),
    }
    if not brief["ok"]:
        brief["error"] = compared.get("error")
        brief["message"] = compared.get("message")
    body = {
        "ok": bool(brief["ok"]),
        "op": "fire_check",
        "sequence": list(SEQUENCE),
        "hotspots": len(rows),
        "hotspot": hotspot,
        "clearest": {key: clearest[key] for key in ("date", "cloud", "id") if key in clearest},
        "imagery_ab": {key: brief[key] for key in ("before", "after", "mode") if brief.get(key) is not None},
        "summary": _summary(len(rows), hotspot, clearest, brief),
    }
    if not brief["ok"]:
        body["error"] = brief.get("error") or "imagery_ab_failed"
        body["code"] = body["error"]
        body["message"] = brief.get("message") or "Before/after imagery did not run."
        body["fix"] = "Raise max_cloud or widen window_days, then retry godseye_feeds op=fire_check."
    return json.dumps(body, ensure_ascii=False)


EXT_OPS = [
    ExtOp(
        tool="godseye_feeds",
        op="fire_check",
        handler=fire_check,
        props={
            "place": {"type": "string", "description": "Place name, or here for the open pane. Omit to use the pane."},
            "bbox": {
                "type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                "description": "[west, south, east, north].",
            },
            "lat": {"type": "number"},
            "lon": {"type": "number"},
            "radius_km": {"type": "number"},
            "window_days": {"type": "integer", "description": "Days of imagery to consider. Default 30."},
            "max_cloud": {"type": "number", "description": "Clearest means at or under this cloud percent. Default 20."},
        },
        blurb="Fire check: FIRMS hotspot, clearest recent image, then imagery before/after. Idempotent.",
    ),
]
