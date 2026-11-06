"""GTFS-RT transit ops on ``godseye_feeds`` and ``godseye_track``.

Upstream ships the Rio de Janeiro SMTR bus feed as the ``transit`` layer. This
module turns that layer on with the live ``set_layer_visibility`` action and
reads its records through the live ``analyst_query`` surface (``layers:
["transit"]`` with a ``region`` or ``radius`` scope). ``get_entity_context``
does not list ``transit`` in its ``layerId`` enum, so the analyst path is the
one that exists. Following a vehicle uses live ``track_entity``; stopping stays
on the existing ``godseye_track`` op=stop (``stop_tracking``).

``live`` and ``now`` are module-level hooks. Tests monkeypatch them; the
functions below take them as explicit arguments and never call the network.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timezone

from .ext_ops import ExtOp

TRANSIT_LAYER = "transit"
MAX_VEHICLES = 50
MAX_ROUTES = 50

# The only GTFS-RT feed upstream ships today. Bounds are the city of Rio de
# Janeiro, not the whole state.
COVERED_REGIONS = (
    {
        "id": "rio-de-janeiro",
        "name": "Rio de Janeiro",
        "bbox": (-23.10, -43.90, -22.70, -43.10),
        "aliases": (
            "rio", "rio de janeiro", "rj", "rio de janeiro, brazil",
            "smtr rio onibus", "smtr-rio-onibus",
        ),
    },
)

_NORMALIZE = re.compile(r"[^a-z0-9]+")
_VEHICLE_LISTS = ("items", "records", "entities", "vehicles", "results", "features", "data")


def _clock() -> float:
    return time.time()


def _live_call(name: str, args: dict | None = None, session_id=None):
    from . import composer

    return composer.live_call(name, args, session_id=session_id)


# Monkeypatch these two names in tests.
live = _live_call
now = _clock


def _fail(error: str, message: str, fix: str | None = None, **extra) -> dict:
    out = {"ok": False, "error": error, "code": error, "message": message}
    if fix:
        out["fix"] = fix
    out.update(extra)
    return out


def _covered_names() -> list[str]:
    return [region["name"] for region in COVERED_REGIONS]


def _regions() -> list[dict]:
    return [
        {"id": region["id"], "name": region["name"], "bbox": list(region["bbox"])}
        for region in COVERED_REGIONS
    ]


def _coverage_error(area=None) -> dict:
    names = ", ".join(_covered_names())
    where = f" for {area!r}" if area else " for that area"
    return _fail(
        "no_transit_coverage",
        f"No GTFS-RT transit coverage{where}. The transit layer covers: {names}.",
        "Pass place='Rio de Janeiro' or a bbox inside its bounds.",
        covered_regions=_covered_names(),
        regions=_regions(),
    )


def _missing_area() -> dict:
    names = ", ".join(_covered_names())
    return _fail(
        "bad_args",
        f"transit needs a place or bbox. The transit layer covers: {names}.",
        "Retry godseye_feeds op=transit with place='Rio de Janeiro' or bbox=[south, west, north, east].",
        covered_regions=_covered_names(),
        regions=_regions(),
    )


def _normal(text) -> str:
    return _NORMALIZE.sub(" ", str(text).casefold()).strip()


def _match_region(place) -> dict | None:
    wanted = _normal(place)
    if not wanted:
        return None
    for region in COVERED_REGIONS:
        names = (region["name"],) + tuple(region["aliases"])
        normalized = [_normal(name) for name in names]
        if wanted in normalized:
            return region
        long_names = [name for name in normalized if len(name) >= 4]
        if any(name in wanted for name in long_names):
            return region
    return None


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bbox(value) -> tuple[tuple[float, float, float, float] | None, str | None]:
    if isinstance(value, dict):
        values = [value.get(key) for key in ("south", "west", "north", "east")]
        if any(item is None for item in values):
            return None, "bbox object needs south, west, north, and east."
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        return None, "bbox must be [south, west, north, east]."
    if len(values) != 4:
        return None, "bbox must have four numbers: south, west, north, east."
    numbers = [_as_number(item) for item in values]
    if any(item is None for item in numbers):
        return None, "bbox values must be numbers."
    south, west, north, east = numbers
    if not (-90 <= south < north <= 90) or not (-180 <= west < east <= 180):
        return None, "bbox is outside WGS84 bounds or has south >= north."
    return (float(south), float(west), float(north), float(east)), None


def _overlaps(one, other) -> bool:
    south, west, north, east = one
    other_south, other_west, other_north, other_east = other
    return south < other_north and north > other_south and west < other_east and east > other_west


def _area(args: dict) -> tuple[dict | None, dict | None]:
    place = args.get("place")
    if isinstance(place, str) and place.strip():
        region = _match_region(place)
        if region is None:
            return None, _coverage_error(place.strip())
        return {
            "region": region,
            "scope": {"kind": "region", "name": region["name"]},
            "bbox": region["bbox"],
            "filter": False,
        }, None
    raw_bbox = args.get("bbox")
    if raw_bbox is not None:
        bounds, error = _bbox(raw_bbox)
        if error:
            return None, _fail("bad_args", error,
                               "Pass bbox as [south, west, north, east].")
        regions = [region for region in COVERED_REGIONS if _overlaps(bounds, region["bbox"])]
        if not regions:
            return None, _coverage_error(raw_bbox)
        south, west, north, east = bounds
        mid_lat = (south + north) / 2.0
        mid_lon = (west + east) / 2.0
        lat_km = (north - south) * 111.0
        lon_km = (east - west) * 111.0 * max(0.2, abs(math.cos(math.radians(mid_lat))))
        return {
            "region": regions[0],
            "scope": {"kind": "radius", "center": {"lat": mid_lat, "lon": mid_lon},
                      "km": max(1.0, max(lat_km, lon_km) / 2.0)},
            "bbox": bounds,
            "filter": True,
        }, None
    return None, _missing_area()


def _payload(raw):
    if isinstance(raw, dict):
        inner = raw.get("result")
        if isinstance(inner, (dict, list)):
            return inner
    return raw


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in _VEHICLE_LISTS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            rows = _rows(value)
            if rows:
                return rows
    return []


def _first(row: dict, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _point(row: dict) -> tuple[object, object]:
    lat = _first(row, "lat", "latitude")
    lon = _first(row, "lon", "lng", "longitude")
    for key in ("position", "location", "coords", "coordinates"):
        nested = row.get(key)
        if isinstance(nested, dict):
            if lat is None:
                lat = _first(nested, "lat", "latitude")
            if lon is None:
                lon = _first(nested, "lon", "lng", "longitude")
    return lat, lon


def _stamp(value) -> str:
    moment = value() if callable(value) else value
    try:
        return datetime.fromtimestamp(float(moment), timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _vehicle(row: dict, clock) -> dict | None:
    lat, lon = _point(row)
    lat, lon = _as_number(lat), _as_number(lon)
    if lat is None or lon is None:
        return None
    vehicle_id = _first(row, "id", "vehicleId", "vehicle_id", "vehicle")
    if vehicle_id is None:
        return None
    route = _first(row, "routeId", "route_id", "route", "line", "routeShortName")
    raw_heading = _first(row, "heading", "headingDeg", "courseDeg", "course", "bearing")
    heading = _as_number(raw_heading) if raw_heading is not None else None
    updated = _first(row, "updated", "updatedAt", "timestamp", "time", "lastSeen",
                     "lastContactAtMs")
    if updated is None:
        updated = _stamp(clock)
    return {
        "id": vehicle_id,
        "route": route,
        "lat": float(lat),
        "lon": float(lon),
        "heading": heading,
        "updated": updated,
        "_name": _route_name(row, route),
    }


def _route_name(row: dict, route_id) -> str:
    name = _first(row, "routeName", "route_name", "routeLongName", "name", "routeLabel")
    return str(name) if name is not None else str(route_id)


def _live_failure(raw, fallback: str) -> dict:
    payload = _payload(raw)
    source = raw if isinstance(raw, dict) else {}
    inner = payload if isinstance(payload, dict) else {}
    error = source.get("error") or inner.get("error") or fallback
    message = source.get("message") or inner.get("message") or str(error)
    out = {"ok": False, "error": error, "code": source.get("code") or inner.get("code") or error,
           "message": message}
    fix = source.get("fix") or inner.get("fix")
    if fix:
        out["fix"] = fix
    return out


def _enable_transit(caller, session_id):
    result = caller("set_layer_visibility", {"layerId": TRANSIT_LAYER, "enabled": True},
                    session_id=session_id)
    if isinstance(result, dict) and result.get("ok") is False:
        return _live_failure(result, "Could not turn on the transit layer.")
    return None


def transit(args: dict, *, live=None, now=None, session_id=None) -> dict:
    """Read the transit layer for a covered place or bbox."""
    caller = live if live is not None else globals()["live"]
    clock = now if now is not None else globals()["now"]
    args = dict(args or {})
    action = str(args.get("action") or "").strip().lower()
    if action not in ("routes", "vehicles"):
        return _fail("bad_args", "transit needs action=routes or action=vehicles.",
                     "Retry godseye_feeds op=transit with action='routes' or 'vehicles'.")

    area, error = _area(args)
    if error:
        return error

    failed = _enable_transit(caller, session_id)
    if failed:
        return failed

    raw = caller("analyst_query", {
        "layers": [TRANSIT_LAYER],
        "scope": area["scope"],
        "limit": MAX_VEHICLES,
    }, session_id=session_id)
    if isinstance(raw, dict) and raw.get("ok") is False:
        return _live_failure(raw, "Could not read the transit layer.")

    rows = []
    for item in _rows(_payload(raw)):
        row = _vehicle(item, clock)
        if row is None:
            continue
        if area["filter"]:
            south, west, north, east = area["bbox"]
            if not (south <= row["lat"] <= north and west <= row["lon"] <= east):
                continue
        rows.append(row)

    common = {"ok": True, "action": action, "layer": TRANSIT_LAYER,
              "region": area["region"]["name"], "count": 0}
    if action == "vehicles":
        common["count"] = len(rows)
        common["vehicles"] = [{key: value for key, value in row.items() if key != "_name"}
                              for row in rows[:MAX_VEHICLES]]
        return common

    by_route: dict[object, dict] = {}
    for row in rows:
        route_id = row["route"]
        if route_id is None:
            continue
        entry = by_route.get(route_id)
        if entry is None:
            entry = {"route_id": route_id, "name": row["_name"], "vehicles": 0}
            by_route[route_id] = entry
        entry["vehicles"] += 1
    routes = sorted(by_route.values(), key=lambda item: (-item["vehicles"], str(item["route_id"])))
    common["count"] = len(routes)
    common["routes"] = routes[:MAX_ROUTES]
    return common


def follow_vehicle(args: dict, *, live=None, session_id=None) -> dict:
    """Follow one transit vehicle on the open pane."""
    caller = live if live is not None else globals()["live"]
    args = dict(args or {})
    vehicle_id = args.get("vehicle_id")
    if vehicle_id is None or not str(vehicle_id).strip():
        return _fail("bad_args", "follow_vehicle needs vehicle_id.",
                     "Pass a vehicle id from godseye_feeds op=transit action=vehicles.")
    vehicle_id = str(vehicle_id).strip()

    failed = _enable_transit(caller, session_id)
    if failed:
        failed.update({"action": "follow_vehicle", "vehicle_id": vehicle_id,
                       "layer": TRANSIT_LAYER})
        return failed

    raw = caller("track_entity", {"query": vehicle_id, "layerId": TRANSIT_LAYER},
                 session_id=session_id)
    if isinstance(raw, dict) and raw.get("ok") is False:
        out = _live_failure(raw, f"Could not follow transit vehicle {vehicle_id!r}.")
        out.update({"action": "follow_vehicle", "vehicle_id": vehicle_id,
                    "layer": TRANSIT_LAYER})
        return out

    out = {"ok": True, "action": "follow_vehicle", "vehicle_id": vehicle_id,
           "layer": TRANSIT_LAYER, "note": "Use godseye_track op=stop to stop following."}
    payload = _payload(raw)
    if isinstance(payload, dict):
        for key in ("pane", "tracking", "entity", "result"):
            if key in payload:
                out[key] = payload[key]
    return out


def _transit_handler(args: dict, **kw) -> str:
    return json.dumps(transit(args, live=live, now=now,
                              session_id=kw.get("session_id")), ensure_ascii=False)


def _follow_handler(args: dict, **kw) -> str:
    return json.dumps(follow_vehicle(args, live=live,
                                     session_id=kw.get("session_id")), ensure_ascii=False)


EXT_OPS = [
    ExtOp(
        tool="godseye_feeds",
        op="transit",
        handler=_transit_handler,
        props={
            "action": {
                "type": "string",
                "enum": ["routes", "vehicles"],
                "required": True,
                "description": "routes groups by route; vehicles lists individual transit vehicles.",
            },
            "place": {
                "type": "string",
                "description": "Covered place name, for example Rio de Janeiro.",
            },
            "bbox": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
                "description": "[south, west, north, east] in WGS84; must overlap a covered region.",
            },
        },
        blurb="GTFS-RT transit (Rio de Janeiro): action=routes or vehicles in a place or bbox.",
    ),
    ExtOp(
        tool="godseye_track",
        op="follow_vehicle",
        handler=_follow_handler,
        props={
            "vehicle_id": {
                "type": "string",
                "required": True,
                "description": "Transit vehicle id from godseye_feeds op=transit action=vehicles. "
                               "Stop with godseye_track op=stop.",
            },
        },
        blurb="Follow one transit vehicle; stop with godseye_track op=stop.",
    ),
]
