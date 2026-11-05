"""Hermes side of "what am I looking at" + measure / reach / compare (pane code: gev/panel_context.js).

Every function takes an injectable ``live`` (name, args, session_id) -> dict so tests run without a pane.
"""
from __future__ import annotations

from . import measure as M

COLORS = ["#5ab4ff", "#5dff8a", "#ffb347", "#ff5a5a"]  # isochrone rings, inner -> outer


def _live(live):
    if live is None:
        from .composer import live_call
        return live_call
    return live


def _payload(res: dict) -> dict:
    """Pane answers come back as the command's payload (dict with ok)."""
    return res.get("result") if isinstance(res.get("result"), dict) and "ok" not in res else res


def pane_context(session_id=None, live=None) -> dict:
    return _payload(_live(live)("hermes_context", {}, session_id))


def live_context(args: dict, session_id=None, live=None) -> dict:
    """The pane's camera, visible bbox, selected pin, last click and drawn shapes, plus (by default)
    a reverse geocode + nearby named places for the focus point ("this" / "here")."""
    ctx = pane_context(session_id, live)
    if not ctx.get("ok"):
        return ctx
    out = {"ok": True, **{k: v for k, v in ctx.items() if k not in ("ok", "action")}}
    f = ctx.get("focus")
    if f and args.get("describe", True):
        try:
            lat, lon = float(f["lat"]), float(f["lon"])
            rng = (ctx.get("camera") or {}).get("rangeM") or 500
            radius = float(args.get("radius_m") or (150 if f.get("kind") in ("pin", "click") or rng < 2500 else min(1500, rng / 4)))
            out["here"] = M.what_is_here(lat, lon, radius_m=radius, nearby=bool(args.get("nearby", True)),
                                         category=args.get("category"), limit=int(args.get("limit") or 8))
        except (TypeError, ValueError, KeyError) as exc:
            out["here"] = {"ok": False, "error": "bad_focus", "message": str(exc)}
    out["hint"] = ("focus = what 'this/here' most likely means: open pin > newest drawn shape > last click > screen centre. "
                   "Ask the user to click a pin, click the spot, or draw a box (toolbar bottom-left) if it is ambiguous.")
    return out


def _resolve_points(args: dict, session_id, live) -> tuple[list | None, dict | None, str]:
    """Points from explicit coords, pin ids/labels, or a drawn shape (``use_drawn``: id or 'latest')."""
    pts = list(args.get("points") or [])
    pins, drawn = args.get("pins") or [], args.get("use_drawn")
    kind = ""
    if pins or drawn:
        ctx = pane_context(session_id, live)
        if not ctx.get("ok"):
            return None, ctx, kind
        if drawn:
            shapes = ctx.get("drawn") or []
            sh = shapes[-1] if str(drawn) in ("latest", "true", "True", "1") and shapes else next((s for s in shapes if s["id"] == str(drawn)), None)
            if not sh:
                return None, {"ok": False, "error": "no_drawing",
                              "message": "No drawn shape on the pane. Ask the user to draw a line, box or polygon "
                                         "(toolbar bottom-left of the globe), or pass points."}, kind
            pts, kind = sh["points"], sh["kind"]
        for q in pins:
            hit = None
            for p in ctx.get("pins") or []:
                if str(q) == str(p.get("id")) or str(q).lower() == str(p.get("label") or "").lower():
                    hit = p
                    break
            if not hit:
                hit = next((p for p in ctx.get("pins") or [] if str(q).lower() in str(p.get("label") or "").lower()), None)
            if not hit:
                return None, {"ok": False, "error": "pin_not_found", "message": f"No pin matching {q!r} on the pane.",
                              "pins": ctx.get("pins")}, kind
            pts.append({"lat": hit["latitude"], "lon": hit["longitude"], "label": hit.get("label")})
    return pts, None, kind


def measure(args: dict, session_id=None, live=None) -> dict:
    pts, err, kind = _resolve_points(args, session_id, live)
    if err:
        return err
    op = args.get("op") or ("area" if kind in ("box", "polygon") else "distance")
    out = M.measure({**args, "op": op, "points": pts})
    if out.get("ok") and args.get("draw", True):
        draw_kind = "polygon" if op == "area" else "line"
        line = out.get("line") if op == "route" else [[M._pt(p)[0], M._pt(p)[1]] for p in pts]
        label = args.get("label") or (f"{out['label']} · {out['minutes']} min {out['mode']}" if op == "route" else out["label"])
        if kind in ("box", "polygon", "line") and args.get("use_drawn"):
            out["drawn"] = {"ok": True, "note": "the drawn shape already shows its own label"}
        else:
            out["drawn"] = _live(live)("hermes_draw", {"action": "add", "kind": draw_kind, "label": label, "color": args.get("color") or "#ffe45a",
                                                        "points": [{"lat": a, "lon": b} for a, b in line]}, session_id)
    return out


def isochrone(args: dict, session_id=None, live=None) -> dict:
    a = dict(args)
    if a.get("lat") is None or a.get("lon") is None:
        ctx = pane_context(session_id, live)
        if not ctx.get("ok"):
            return {**ctx, "message": (ctx.get("message") or "") + " (or pass lat/lon)"}
        f = ctx.get("focus") or {}
        if f.get("lat") is None:
            return {"ok": False, "error": "no_focus", "message": "Pass lat/lon or select a pin / click a spot on the globe."}
        a["lat"], a["lon"] = f["lat"], f["lon"]
    out = M.isochrone(a)
    if out.get("ok") and a.get("draw", True):
        drawn = []
        for i, c in enumerate(reversed(out["contours"])):  # outer ring first
            approx = " (approx.)" if out.get("approximate") else ""
            drawn.append(_live(live)("hermes_draw", {"action": "add", "id": f"reach-{c['minutes']}-{out['mode']}", "kind": "polygon", "labelAt": "top",
                                                      "label": f"{c['minutes']} min {out['mode']}{approx}",
                                                      "color": COLORS[(len(out["contours"]) - 1 - i) % len(COLORS)],
                                                      "points": [{"lat": p[0], "lon": p[1]} for p in c["polygon"]]}, session_id).get("ok"))
        out["drawn"] = all(drawn)
    for c in out.get("contours", []):  # keep the tool answer small; the polygon is on the pane
        c["points"] = len(c["polygon"])
        if not args.get("include_polygon"):
            c.pop("polygon")
    return out


def draw(args: dict, session_id=None, live=None) -> dict:
    return _payload(_live(live)("hermes_draw", dict(args), session_id))


def compare(args: dict, session_id=None, live=None) -> dict:
    action = str(args.get("action") or "start")
    if action == "list_dates":
        rels = M.wayback_releases()
        if not rels:
            return {"ok": False, "error": "wayback_unavailable", "message": "Esri World Imagery Wayback config is unreachable."}
        return {"ok": True, "count": len(rels), "newest": rels[0], "oldest": rels[-1],
                "dates": [r["date"] for r in rels][:200]}
    if action != "start":
        return _payload(_live(live)("hermes_compare", {"action": action, **({"position": args["position"]} if "position" in args else {})}, session_id))
    sides = {}
    for side, default in (("left", "wayback:2014"), ("right", "current")):
        s = M.compare_layer(args.get(side) or default)
        if s["kind"] == "error":
            return {"ok": False, "error": s["error"], "message": s["message"]}
        if s["kind"] == "stack":
            return {"ok": False, "error": "unsupported_side",
                    "message": f"{s['label']!r}: God's Eye View shows one map stack at a time, so map stacks cannot be split. "
                               "Use 'current' (whatever the globe shows now), 'osm', or 'wayback:YYYY[-MM]' for historical Esri imagery."}
        sides[side] = s
    res = _payload(_live(live)("hermes_compare", {"action": "start", **sides, "position": args.get("position", 0.5)}, session_id))
    if res.get("ok"):
        res["sides"] = {k: {x: v[x] for x in ("label", "date", "release") if x in v} for k, v in sides.items()}
        res["note"] = ("Drag the yellow divider (or call action=position/toggle). Wayback dates are Esri's capture-release dates; "
                       "the imagery under a spot may be older than its release. Photoreal 3D tiles are hidden while comparing.")
    return res
