"""Safe dolly for godseye_nav op=fly_route.

The pane's own fly_route walks whatever route is already drawn. A 1,600-point
street trace plus Street Level tiles froze the page. This plans a thinner
line, sends at most CHUNK_POINTS points per command, and by default turns
Street Level off and coarsens the globe until the dolly finishes.
"""
from __future__ import annotations

import threading

from .routegeom import CHUNK_POINTS, chunks, mean_spacing_m, simplify

STREET_LAYER = "street-level"
_halt_lock = threading.Lock()
_halted: set[str] = set()


def halt(session_id: str | None) -> None:
    """The pane reloaded. The dolly loop stops after the current chunk returns."""
    with _halt_lock:
        _halted.add(str(session_id or ""))


def _take_halt(session_id: str | None) -> bool:
    with _halt_lock:
        return str(session_id or "") in _halted


def _clear_halt(session_id: str | None) -> None:
    with _halt_lock:
        _halted.discard(str(session_id or ""))


def _route_specs(annotations) -> list[dict]:
    specs = []
    for ann in annotations or []:
        spec = ann.get("spec") if isinstance(ann, dict) and isinstance(ann.get("spec"), dict) else ann
        if isinstance(spec, dict) and str(spec.get("type") or "").lower() == "route":
            specs.append(spec)
    return specs


def resolve_points(args: dict | None, annotations) -> tuple[list, str | None]:
    """Points from the call, else the newest saved route (optional label match)."""
    from .routegeom import as_points
    args = args or {}
    label = str(args.get("label") or "").strip() or None
    direct = as_points(args.get("points") or args.get("waypoints") or args.get("stops"))
    if len(direct) >= 2:
        return direct, label
    specs = _route_specs(annotations)
    if label:
        needle = label.lower()
        matched = [spec for spec in specs if needle in str(spec.get("label") or "").lower()]
        if matched:
            specs = matched
    if not specs:
        return [], label
    spec = specs[-1]
    return as_points(spec.get("points") or spec.get("waypoints") or spec.get("stops")), (
        label or (str(spec.get("label")).strip() if spec.get("label") else None))


def plan(points, *, step_m: float | None = None, epsilon_m: float | None = None,
         chunk_size: int = CHUNK_POINTS) -> dict:
    """Thin a dense line and split it. A short, already-sparse line is one chunk."""
    from .routegeom import FLY_EPSILON_M, FLY_STEP_M
    raw = list(points or [])
    step = float(step_m or FLY_STEP_M)
    epsilon = float(epsilon_m if epsilon_m is not None else FLY_EPSILON_M)
    dense = len(raw) > chunk_size or (len(raw) > 80 and mean_spacing_m(raw) < step * 0.75)
    thinned = simplify(raw, step, epsilon) if dense else list(raw)
    parts = chunks(thinned, chunk_size)
    return {
        "pointsIn": len(raw),
        "pointsOut": len(thinned),
        "dense": dense,
        "chunks": parts,
        "chunkCount": len(parts),
        "capped": all(len(part) <= chunk_size for part in parts),
    }


def _relay():
    from . import live
    return live.current()


def _annotations(relay, session_id):
    if relay is None:
        return []
    targets = relay.targets(session_id)
    pane = targets[0] if targets else None
    key = relay.scene_key(session_id, pane)
    return relay.scenes.annotations(key)


def _street_on(relay, session_id) -> bool:
    if relay is None:
        return False
    targets = relay.targets(session_id)
    for pane in targets:
        layers = (pane.snapshot or {}).get("layers") or []
        if STREET_LAYER in layers:
            return True
    pane = targets[0] if targets else None
    key = relay.scene_key(session_id, pane)
    return STREET_LAYER in (relay.scenes.scene(key).get("layers") or [])


def _call(name: str, args: dict, session_id, *, record: bool, fanout: bool = False) -> dict:
    from . import composer, live
    relay = live.current()
    if relay is not None:
        return relay.submit(name, args, session_id=session_id, record=record, fanout=fanout)
    body = {"name": name, "args": args or {}}
    if session_id:
        body["session_id"] = str(session_id)
    if fanout:
        body["fanout"] = True
    if record is False:
        body["record"] = False
    return live.call("/cmd", composer._token(), body)


def _motion(relay, session_id, motion) -> None:
    if relay is None:
        return
    targets = relay.targets(session_id)
    pane = targets[0] if targets else None
    key = relay.scene_key(session_id, pane)
    relay.scenes.note_motion(key, motion)


def _pairs(points) -> list[dict]:
    out = []
    for point in points:
        if isinstance(point, dict):
            out.append({"lat": point["lat"], "lon": point["lon"]})
        else:
            out.append({"lat": point[0], "lon": point[1]})
    return out


def run(args: dict | None, session_id: str | None = None, fanout: bool = False, *,
        relay=None, call=None) -> dict:
    """Dolly a saved route. Long routes go out in chunks of about 300 points."""
    args = dict(args or {})
    lighten = args.get("lighten") is not False
    speed = args.get("speed") if args.get("speed") in ("slow", "normal", "fast") else None
    relay = relay if relay is not None else _relay()

    def _default_call(name, payload, record):
        if relay is not None:
            return relay.submit(name, payload, session_id=session_id, record=record, fanout=fanout)
        return _call(name, payload, session_id, record=record, fanout=fanout)

    caller = call or _default_call
    _clear_halt(session_id)
    points, label = resolve_points(args, _annotations(relay, session_id))
    planned = plan(points) if len(points) >= 2 else None
    use_chunks = bool(planned and (planned["dense"] or planned["chunkCount"] > 1 or planned["pointsOut"] > CHUNK_POINTS))
    was_on = _street_on(relay, session_id) if lighten else False
    if relay is not None and (use_chunks or lighten):
        relay.hold_activity(session_id, "fly_route", {"label": label, "chunks": (planned or {}).get("chunkCount") or 1})
    motion = {"kind": "fly_route", "state": "running", "label": label, "index": 0,
              "total": (planned or {}).get("chunkCount") or 1}
    if use_chunks or lighten:
        _motion(relay, session_id, motion)
    quiet = False
    try:
        if lighten:
            quiet = True
            caller("hermes_route_light", {"on": True, "perf": "embed"}, False)
            caller("set_layer_visibility", {"layerId": STREET_LAYER, "enabled": False}, False)
        if not use_chunks:
            upstream = {k: v for k, v in (("label", label), ("speed", speed)) if v}
            result = caller("fly_route", upstream, True)
            if isinstance(result, dict) and result.get("ok"):
                _motion(relay, session_id, None)
            elif isinstance(result, dict):
                _motion(relay, session_id, {**motion, "state": "interrupted"})
            return _summarize(result, planned, lighten, label, chunked=False)
        sent = []
        for index, part in enumerate(planned["chunks"]):
            if _take_halt(session_id):
                interrupted = _lost("The dolly was interrupted because the pane reloaded.", label, index, planned["chunkCount"])
                _motion(relay, session_id, {**motion, "state": "interrupted", "index": index})
                return _summarize(interrupted, planned, lighten, label, chunked=True, sent=sent)
            _motion(relay, session_id, {**motion, "state": "running", "index": index, "total": planned["chunkCount"]})
            if relay is not None:
                relay.hold_activity(session_id, "fly_route", {
                    "label": label, "index": index, "total": planned["chunkCount"]})
            result = caller("hermes_fly_chunk", {
                "points": _pairs(part), "speed": speed or "normal",
                "index": index, "chunks": planned["chunkCount"], "label": label,
            }, False)
            sent.append({"index": index, "points": len(part), "ok": bool(isinstance(result, dict) and result.get("ok"))})
            if not (isinstance(result, dict) and result.get("ok")):
                _motion(relay, session_id, {**motion, "state": "interrupted", "index": index})
                return _summarize(result if isinstance(result, dict) else {"ok": False, "error": "fly_route failed"},
                                  planned, lighten, label, chunked=True, sent=sent)
        _motion(relay, session_id, None)
        return _summarize({
            "ok": True, "action": "fly_route", "label": label,
            "pointsIn": planned["pointsIn"], "pointsOut": planned["pointsOut"],
            "chunks": planned["chunkCount"], "lighten": lighten,
        }, planned, lighten, label, chunked=True, sent=sent)
    finally:
        if quiet:
            if was_on:
                caller("set_layer_visibility", {"layerId": STREET_LAYER, "enabled": True}, False)
            caller("hermes_route_light", {"on": False}, False)
        if relay is not None:
            relay.release_activity(session_id, "fly_route")


def _lost(message: str, label, index: int, total: int) -> dict:
    return {
        "ok": False, "error": "pane_lost", "code": "pane_lost", "message": message,
        "fix": "Ask the user to reopen the Eye Of Hermes (EOH) pane and keep it visible. The saved scene restores on its own. Offer to start the dolly again with godseye_nav op=fly_route.",
        "lastActivity": {"name": "fly_route", "detail": {"label": label, "index": index, "total": total}},
    }


def _summarize(result: dict, planned, lighten: bool, label, *, chunked: bool, sent=None) -> dict:
    out = dict(result or {"ok": False, "error": "fly_route failed"})
    if planned:
        out.setdefault("pointsIn", planned["pointsIn"])
        out.setdefault("pointsOut", planned["pointsOut"])
        out.setdefault("chunks", planned["chunkCount"])
    out["lighten"] = lighten
    out["chunked"] = chunked
    if label and "label" not in out:
        out["label"] = label
    if sent is not None:
        out["sent"] = sent
    return out
