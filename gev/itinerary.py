"""Build one route through pins, draw it, and optionally dolly or walk it.

``fly_route`` matches a route id exactly, then an exact label, and only then
a fuzzy label when that fuzzy label hits one route.
"""
from __future__ import annotations


def _spec(ann: dict) -> dict:
    if isinstance(ann, dict) and isinstance(ann.get("spec"), dict):
        return ann["spec"]
    return ann if isinstance(ann, dict) else {}


def _ids(ann: dict) -> set[str]:
    spec = _spec(ann)
    found = set()
    for value in (ann.get("id"), ann.get("paneId"), spec.get("clientId"), spec.get("id")):
        if value:
            found.add(str(value))
    return found


def _point_of(ann: dict) -> tuple[float, float] | None:
    spec = _spec(ann)
    lat, lon = spec.get("latitude"), spec.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not isinstance(lat, bool):
        return float(lat), float(lon)
    return None


def resolve_pins(pins, annotations) -> tuple[list[tuple[float, float]], dict | None]:
    points = []
    missing = []
    anns = list(annotations or [])
    for item in pins or []:
        if isinstance(item, dict):
            lat = item.get("lat", item.get("latitude"))
            lon = item.get("lon", item.get("longitude"))
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                points.append((float(lat), float(lon)))
                continue
            item = item.get("id") or item.get("label")
        key = str(item or "")
        hit = None
        for ann in anns:
            if key and key in _ids(ann):
                hit = ann
                break
        if hit is None and key:
            for ann in anns:
                label = str(_spec(ann).get("label") or "")
                if label == key:
                    hit = ann
                    break
        point = _point_of(hit) if hit else None
        if point:
            points.append(point)
        else:
            missing.append(key or "?")
    if missing:
        return [], {"ok": False, "error": "not_found",
                    "message": "Some itinerary pins were not in the scene: " + ", ".join(missing[:8]),
                    "fix": "Pass pin ids from godseye_annotate op=list, or {lat, lon} coordinates."}
    if len(points) < 2:
        return [], {"ok": False, "error": "bad_points",
                    "message": "An itinerary needs at least two pins or coordinates.",
                    "fix": "Pass pins as a list of ids or {lat, lon} objects, and mode drive or walk."}
    return points, None


def choose_route(args: dict | None, annotations) -> tuple[list, str | None, dict | None]:
    """Points for a dolly. Exact id, then exact label, then a unique fuzzy label."""
    from .routegeom import as_points
    args = args or {}
    route_id = str(args.get("id") or args.get("routeId") or "").strip()
    label = str(args.get("label") or "").strip() or None
    direct = as_points(args.get("points") or args.get("waypoints") or args.get("stops"))
    if len(direct) >= 2 and not route_id:
        return direct, label, None
    specs = []
    for ann in annotations or []:
        spec = _spec(ann)
        if str(spec.get("type") or "").lower() == "route":
            specs.append((ann, spec))
    if route_id:
        matched = [(ann, spec) for ann, spec in specs if route_id in _ids(ann) or route_id == str(spec.get("clientId") or "")]
        if len(matched) != 1:
            return [], label, {
                "ok": False, "error": "not_found" if not matched else "ambiguous",
                "message": f"No single route has id {route_id!r}.",
                "fix": "Pass the route id from godseye_annotate op=list, or explicit coordinates.",
                "routes": [_brief(ann, spec) for ann, spec in specs[:8]],
            }
        ann, spec = matched[0]
        return as_points(spec.get("points") or spec.get("waypoints")), label or spec.get("label"), None
    if label:
        needle = label.lower()
        exact = [(ann, spec) for ann, spec in specs if str(spec.get("label") or "").strip().lower() == needle]
        if len(exact) == 1:
            spec = exact[0][1]
            return as_points(spec.get("points") or spec.get("waypoints")), spec.get("label") or label, None
        if len(exact) > 1:
            return [], label, _ambiguous(exact)
        fuzzy = [(ann, spec) for ann, spec in specs if needle in str(spec.get("label") or "").lower()]
        if len(fuzzy) == 1:
            spec = fuzzy[0][1]
            return as_points(spec.get("points") or spec.get("waypoints")), spec.get("label") or label, None
        if len(fuzzy) > 1:
            return [], label, _ambiguous(fuzzy)
        return [], label, {
            "ok": False, "error": "not_found",
            "message": f"No route is labeled {label!r}.",
            "fix": "Pass id, an exact label, or points. Fuzzy matching is only used when one route contains the label.",
            "routes": [_brief(ann, spec) for ann, spec in specs[:8]],
        }
    if not specs:
        return [], None, None
    spec = specs[-1][1]
    return as_points(spec.get("points") or spec.get("waypoints")), (
        str(spec.get("label")).strip() if spec.get("label") else None), None


def _brief(ann: dict, spec: dict) -> dict:
    return {"id": ann.get("id"), "label": spec.get("label"), "clientId": spec.get("clientId")}


def _ambiguous(pairs) -> dict:
    return {
        "ok": False, "error": "ambiguous",
        "message": "More than one route matches that label. Labels are matched exactly before a fuzzy match.",
        "fix": "Pass id or explicit coordinates. Do not rely on a substring that hits two routes.",
        "routes": [_brief(ann, spec) for ann, spec in pairs[:8]],
    }


def run(args: dict | None, session_id: str | None = None, *, relay=None, route_fn=None,
        draw=None, dolly=None, street=None) -> dict:
    args = dict(args or {})
    mode = str(args.get("mode") or "drive").lower()
    if mode not in ("drive", "walk"):
        return {"ok": False, "error": "bad_args", "message": "itinerary mode must be drive or walk.",
                "fix": "Retry godseye_nav op=itinerary with mode drive or walk."}
    annotations = []
    if relay is not None:
        from .streetwalk import _annotations, _scene_key
        annotations = _annotations(relay, session_id)
        _scene_key(relay, session_id)
    points, err = resolve_pins(args.get("pins"), annotations)
    if err:
        return err
    if route_fn is None:
        from .measure import route as route_fn
    routed = route_fn(points, mode)
    if not isinstance(routed, dict) or not routed.get("ok"):
        return routed if isinstance(routed, dict) else {"ok": False, "error": "routing_unavailable"}
    line = routed.get("line") or [[lat, lon] for lat, lon in points]
    label = str(args.get("label") or f"{mode} itinerary")[:80]
    mark = {"type": "route", "label": label, "points": [{"lat": lat, "lon": lon} for lat, lon in line],
            "clientId": args.get("id") or args.get("clientId")}
    drawn = None
    if draw is not None:
        drawn = draw([mark])
    elif relay is not None:
        from .streetwalk import _call
        drawn = _call(relay, session_id, "annotate_map", {"annotations": [mark], "persist": True})
    out = {
        "ok": True, "op": "itinerary", "mode": mode, "label": label,
        "meters": routed.get("meters"), "minutes": routed.get("minutes"),
        "points": len(line), "mark": label,
    }
    if isinstance(drawn, dict) and drawn.get("ok") is False:
        out["ok"] = False
        out["draw"] = {"ok": False, "error": drawn.get("error")}
        return out
    if args.get("dolly") is True:
        from . import flyroute
        out["dolly"] = dolly(line, label) if dolly else flyroute.run(
            {"points": line, "label": label, "lighten": args.get("lighten", True)},
            session_id=session_id, relay=relay)
    if args.get("streetStops") is True or args.get("street") is True:
        from . import streetstep
        out["street"] = street(line, label) if street else streetstep.run(
            {"street": "route", "points": [{"lat": lat, "lon": lon} for lat, lon in line], "label": label},
            session_id=session_id, relay=relay)
    return out
