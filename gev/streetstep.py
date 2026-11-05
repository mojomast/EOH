"""godseye_nav op=street_level: open the viewer and step one panorama at a time.

``at`` opens the nearest panorama at a point. ``route`` samples a saved route
and opens the first panorama. ``next`` and ``prev`` move along that route.
This is one hop per call. ``op=street_walk`` still dwells and advances on its own.
"""
from __future__ import annotations

from .flyroute import resolve_points
from .streetlevel import nearest

_CURSORS: dict[str, dict] = {}


def _cursor(session: str) -> dict:
    return _CURSORS.setdefault(session, {"index": 0, "points": [], "label": None})


def reset(session: str | None = None) -> None:
    if session is None:
        _CURSORS.clear()
    else:
        _CURSORS.pop(str(session), None)


def _point(args: dict) -> tuple[float, float] | None:
    lat, lon = args.get("lat", args.get("latitude")), args.get("lon", args.get("longitude"))
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    at = args.get("at")
    if isinstance(at, dict):
        lat, lon = at.get("lat", at.get("latitude")), at.get("lon", at.get("longitude"))
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return float(lat), float(lon)
    return None


def run(args: dict | None, session_id: str | None = None, *, relay=None, lookup=None, hop=None) -> dict:
    args = dict(args or {})
    action = str(args.get("op") or args.get("street") or "at").lower()
    if action not in ("at", "next", "prev", "route"):
        return {"ok": False, "error": "bad_args",
                "message": "street_level action must be at, next, prev, or route.",
                "fix": "Retry godseye_nav op=street_level with street set to at, next, prev, or route."}
    session = str(session_id or "")
    cursor = _cursor(session)
    lookup = lookup or nearest
    if action in ("at", "route"):
        if action == "at":
            point = _point(args)
            points = [point] if point else []
            label = args.get("label")
        else:
            annotations = []
            if relay is not None:
                from .streetwalk import _annotations
                annotations = _annotations(relay, session_id)
            points, label = resolve_points(args, annotations)
        if len(points) < 1:
            return {"ok": False, "error": "bad_points",
                    "message": "street_level needs a lat/lon, or a saved route for street=route.",
                    "fix": "Pass lat and lon, or label / points for street=route."}
        cursor["points"] = list(points)
        cursor["index"] = 0
        cursor["label"] = label
    else:
        if not cursor.get("points"):
            return {"ok": False, "error": "no_route",
                    "message": "There is no street-level position to step from.",
                    "fix": "Call godseye_nav op=street_level with street=at or street=route first."}
        step = 1 if action == "next" else -1
        nxt = int(cursor.get("index") or 0) + step
        if nxt < 0 or nxt >= len(cursor["points"]):
            return {"ok": False, "error": "end", "message": "No further panorama in that direction.",
                    "index": cursor.get("index") or 0, "total": len(cursor["points"]),
                    "fix": "Step the other way, or start again with street=at or street=route."}
        cursor["index"] = nxt
    index = int(cursor.get("index") or 0)
    lat, lon = cursor["points"][index][0], cursor["points"][index][1]
    found = lookup(lat, lon, prefer=args.get("provider"))
    if not isinstance(found, dict) or not found.get("ok"):
        return found if isinstance(found, dict) else {"ok": False, "error": "no_pano"}
    heading = None
    if index + 1 < len(cursor["points"]):
        from .routegeom import bearing_deg
        heading = round(bearing_deg((lat, lon), tuple(cursor["points"][index + 1])), 1)
    body = {
        "ok": True, "op": "street_level", "street": action, "index": index,
        "total": len(cursor["points"]), "lat": lat, "lon": lon,
        "provider": found.get("provider"), "panoId": found.get("panoId"),
        "label": cursor.get("label"),
    }
    if heading is not None:
        body["heading"] = heading
    if hop is not None:
        body["pane"] = hop({
            "lat": found.get("lat", lat), "lon": found.get("lon", lon),
            "heading": heading, "panoId": found.get("panoId"), "provider": found.get("provider"),
        })
    elif relay is not None:
        from .streetwalk import _call
        body["pane"] = _call(relay, session_id, "hermes_street_hop", {
            "lat": found.get("lat", lat), "lon": found.get("lon", lon),
            "heading": heading, "panoId": found.get("panoId"), "provider": found.get("provider"),
        })
    return body
