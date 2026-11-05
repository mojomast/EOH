"""Composite features on top of the data tools and the live globe: brief-and-show, home view,
share links, the doctor health check, and the /gev slash command."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
import urllib.request
from pathlib import Path

from . import config, home, policy

_ROOT = Path(__file__).resolve().parent.parent
_SHARE_SCRIPT = _ROOT / "scripts" / "share_link.mjs"
MILITARY = re.compile(r"\b(military|milair|base|bases|navy|naval|air ?force|army|defen[cs]e|warships?|fighters?|"
                      r"carrier|installations?)\b", re.I)
# Fallback coordinates when upstream geocoding is unavailable (fly_to_location preset cities and a few more).
PLACES = {"tokyo": (35.6812, 139.7671), "london": (51.5074, -0.1278), "paris": (48.8566, 2.3522),
          "new york": (40.7128, -74.006), "nyc": (40.7128, -74.006), "san francisco": (37.7749, -122.4194),
          "sf": (37.7749, -122.4194), "austin": (30.2672, -97.7431), "dubai": (25.2048, 55.2708),
          "washington": (38.9072, -77.0369), "dc": (38.9072, -77.0369), "san diego": (32.7157, -117.1611),
          "los angeles": (34.0522, -118.2437), "taiwan": (23.6978, 120.9605), "singapore": (1.3521, 103.8198),
          "sydney": (-33.8688, 151.2093), "montreal": (45.5019, -73.5674), "toronto": (43.6532, -79.3832),
          "honolulu": (21.3069, -157.8583), "norfolk": (36.8508, -76.2859)}


def _token() -> str:
    from . import bridge
    return bridge.ensure_token()


def live_call(name: str, args: dict | None = None, session_id: str | None = None, fanout: bool = False,
              pane_id: str | None = None) -> dict:
    from . import live
    body = {"name": name, "args": args or {}}
    if session_id:
        body["session_id"] = str(session_id)
    if pane_id:
        body["pane_id"] = pane_id
    if fanout:
        body["fanout"] = True
    return live.call("/cmd", _token(), body)


def relay_status() -> dict:
    from . import live
    out = live.call("/status", _token(), None, 5)
    return out if "paneOpen" in out else {"paneOpen": False, "panes": [], "error": out.get("error"),
                                          "message": out.get("message")}


def mcp_call(name: str, args: dict) -> dict:
    from . import mcp_client
    return mcp_client.client().call_tool(name, args)


def _structured(result: dict) -> dict:
    sc = (result or {}).get("structuredContent")
    if isinstance(sc, dict):
        return sc
    for c in (result or {}).get("content", []):
        try:
            data = json.loads(c.get("text", ""))
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            continue
    return {}


def _text(result: dict) -> str:
    parts = [c.get("text", "") for c in (result or {}).get("content", []) if c.get("type") == "text"]
    return parts[0] if parts else ""


def hits_from(data: dict, top_n: int = 5) -> list[dict]:
    """Rows with coordinates across every brief section, nearest first."""
    rows = []
    for key, section in (data.get("sections") or {}).items():
        for row in ((section or {}).get("data") or {}).get("rows") or []:
            lat, lon = row.get("lat", row.get("latitude")), row.get("lon", row.get("longitude"))
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            label = next((str(row[k]).strip(". ") for k in ("callsign", "name", "title", "place", "label", "id")
                          if row.get(k)), key)
            rows.append({"section": key, "label": label[:40], "lat": lat, "lon": lon,
                         "distance_km": row.get("distance_km")})
    rows.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"] or 0))
    return rows[:max(0, int(top_n))]


DRAWER_KEYS = ("title", "description_md", "images", "links")


def hit_drawer(hit: dict, place: str, intent: str | None = None) -> dict:
    """Default pin-drawer content for a brief hit, shaped by the user's intent when given."""
    from . import enrich
    sec = str(hit.get("section") or "")
    lines = [f"**{sec.replace('_', ' ').title()}** near {place}" if sec else f"Near {place}"]
    if SECTION_SOURCES.get(sec):
        lines.append(f"Source: {SECTION_SOURCES[sec]}")
    if isinstance(hit.get("distance_km"), (int, float)):
        lines.append(f"Distance: {hit['distance_km']:.1f} km")
    lines.append(f"Position: `{hit['lat']:.5f}, {hit['lon']:.5f}`")
    base = {
        "type": "pin", "title": str(hit.get("label") or sec)[:120],
        "label": hit.get("label") or sec, "latitude": hit.get("lat"), "longitude": hit.get("lon"),
        "description_md": "\n\n".join(lines),
        "section": sec, "source": SECTION_SOURCES.get(sec), "distance_km": hit.get("distance_km"),
        **{k: hit.get(k) for k in ("confidence", "magnitude", "mag", "frp", "callsign", "mmsi",
                                   "shipname", "detection_time", "acq_date", "time", "depth_km")
           if hit.get(k) is not None},
    }
    # Keep the classic brief body; enrich only adds intent-relevant facts not already present.
    return enrich.ensure_drawer(base, intent=intent, mode="light")




# Section key -> short public data-source label for caption lines (never "worldwide").
SECTION_SOURCES = {
    "aircraft": "OpenSky/adsb.lol", "military_aircraft": "OpenSky/adsb.lol",
    "vessels": "AISStream", "ships": "AISStream",
    "fires": "NASA FIRMS", "earthquakes": "USGS", "cyclones": "NOAA",
    "weather": "Open-Meteo", "satellites": "CelesTrak",
}


def caption_scope(data: dict | None = None, scope: str | None = None) -> str:
    """Count scope for captions: 'in view' (live camera) or 'queried extent' (brief area). Never 'worldwide'."""
    if scope in ("in view", "queried extent"):
        return scope
    if isinstance(data, dict) and (data.get("count_scope") or data.get("countScope")):
        raw = str(data.get("count_scope") or data.get("countScope")).lower()
        if "view" in raw:
            return "in view"
        if "world" in raw:
            return "queried extent"  # never claim whole-world coverage
        if "extent" in raw or "quer" in raw or "area" in raw or "radius" in raw:
            return "queried extent"
    return "queried extent"


def caption_for(label: str, data: dict, scope: str | None = None) -> str:
    bits, sources = [], []
    for key, section in (data.get("sections") or {}).items():
        d = (section or {}).get("data") or {}
        if isinstance(d.get("total"), int):
            bits.append(f"{d['total']} {key.replace('_', ' ')}")
        elif key == "weather" and (d.get("weather") or {}).get("conditions"):
            w = d["weather"]
            temp = f" {round(w['temperature_c'])}°C" if isinstance(w.get("temperature_c"), (int, float)) else ""
            bits.append(f"{w['conditions']}{temp}")
        src = d.get("source") or (section or {}).get("source") or SECTION_SOURCES.get(key)
        if src and src not in sources:
            sources.append(str(src)[:40])
    body = " · ".join(bits) if bits else ""
    text = f"{label}: {body}" if body else label
    sc = caption_scope(data, scope)
    # Never say worldwide; always name the count scope when we reported totals.
    if bits:
        text = f"{text} ({sc})"
    if sources:
        text = f"{text} · {', '.join(sources[:3])}"
    return text[:160]


def _coords(place: str):
    return PLACES.get((place or "").strip().lower())


def brief_and_show(args: dict, session_id: str | None = None, live=None, mcp=None) -> dict:
    live, mcp = live or live_call, mcp or mcp_call
    place = str(args.get("place") or "").strip()
    question = str(args.get("question") or "")
    top_n = max(0, min(24, int(args.get("top_n", 5))))
    lat, lon = args.get("lat"), args.get("lon")
    reason = policy.refuse_reason({"place": place, "question": question})
    if reason:
        return {"error": reason}
    if not place and lat is None:
        return {"error": "Give a place (or lat and lon)."}
    military = args.get("military")
    if military is None:
        military = bool(MILITARY.search(f"{place} {question}"))
    tool = "military_awareness" if military else "situation_brief"
    radius = args.get("radius_km")

    def request(where: dict) -> dict:
        if tool == "military_awareness":
            a = {"location": where}
            if radius:
                a["radius_km"] = min(250, float(radius))
            return mcp(tool, a)
        area = dict(where)
        if "lat" in area:
            area["radius_km"] = min(2000, float(radius or 100))
        return mcp(tool, {"area": area})

    notes = []
    where = {"lat": float(lat), "lon": float(lon)} if lat is not None and lon is not None else {"place": place}
    try:
        result = request(where)
    except Exception as exc:
        result = {"isError": True, "content": [{"type": "text", "text": f"God's Eye View call failed: {exc}"}]}
    if result.get("isError") and "place" in where:  # upstream geocoder down: retry with coordinates
        pt = _coords(place)
        if not pt:  # let the open globe geocode it
            flown = live("fly_to_location", {"query": place, "waitForArrival": True}, session_id)
            if flown.get("ok"):
                cam = (_structured_live(live("get_current_view_state", {}, session_id)).get("camera") or {})
                if isinstance(cam.get("latitude"), (int, float)):
                    pt = (cam["latitude"], cam["longitude"])
        if pt:
            notes.append("place lookup was unavailable; used coordinates for " + place)
            result = request({"lat": pt[0], "lon": pt[1]})
    if result.get("isError"):
        return {"error": _text(result) or "brief failed", "tool": tool}
    data = _structured(result)
    brief = _text(result)
    view = data.get("view") or {}
    cam = view.get("camera") or {}
    hits = hits_from(data, top_n)
    label = place or f"{cam.get('lat')}, {cam.get('lon')}"
    caption = caption_for(label, data)
    shown = {"globe_open": False, "flew": False, "layers": [], "annotated": 0, "caption": None}
    if isinstance(cam.get("lat"), (int, float)):
        fly = live("fly_to_location", {"latitude": cam["lat"], "longitude": cam["lon"],
                                        "rangeM": max(100, min(20_000_000, cam.get("altitude_m") or 200_000)),
                                        "waitForArrival": True}, session_id)
        if fly.get("error") in ("no_pane", "relay_unavailable"):
            shown["message"] = ("The Eye Of Hermes (EOH) globe isn't open, so nothing was shown. Open the Eye Of Hermes "
                                "(EOH) pane in Hermes Desktop or the Webapp to see it.")
        elif fly.get("error") == "pane_busy":
            shown["message"] = fly.get("message")
        else:
            shown.update(globe_open=True, flew=bool(fly.get("ok")), pane=fly.get("pane"))
            errors = []

            def note(step, r):
                if not r.get("ok"):
                    errors.append(f"{step}: {r.get('message') or r.get('error') or (r.get('result') or {}).get('error') or 'failed'}"[:160])
                return bool(r.get("ok"))
            if not fly.get("ok"):
                note("fly", fly)
            for layer in view.get("layers") or []:
                r = live("set_layer_visibility", {"layerId": layer, "enabled": True}, session_id)
                if note(f"layer {layer}", r):
                    shown["layers"].append(layer)
            if hits:
                details = [d if isinstance(d, dict) else {} for d in (args.get("pin_details") or [])]
                intent = args.get("intent") or args.get("context") or question or place
                marks = [{"type": "pin", "latitude": h["lat"], "longitude": h["lon"], "label": h["label"],
                          "color": "red" if military else "amber", **hit_drawer(h, label, intent=intent),
                          **({k: v for k, v in details[i].items() if k in DRAWER_KEYS} if i < len(details) else {})}
                         for i, h in enumerate(hits)]
                r = live("annotate_map", {"annotations": marks, "intent": intent, "persist": True}, session_id)
                shown["annotated"] = ((r.get("result") or {}).get("drawn") or len(marks)) if note("annotate", r) else 0
            r = live("hermes_caption", {"text": caption}, session_id)
            shown["caption"] = caption if note("caption", r) else None
            if errors:
                shown["errors"] = errors
    out = {"tool": tool, "brief": brief, "hits": hits, "shown": shown}
    if notes:
        out["notes"] = notes
    return out


def _structured_live(res: dict) -> dict:
    r = (res or {}).get("result")
    return r if isinstance(r, dict) else {}


# -- home view -----------------------------------------------------------------------------------
def view_from_state(state: dict) -> dict:
    cam = state.get("camera") or {}
    layers = [l.get("id") for l in state.get("layers") or [] if isinstance(l, dict) and l.get("enabled") is not False and l.get("id")]
    return {"lat": cam.get("latitude"), "lon": cam.get("longitude"), "altitude_m": cam.get("heightM"),
            "heading": cam.get("heading") if isinstance(cam.get("heading"), (int, float)) else cam.get("headingDeg"),
            "pitch": cam.get("pitch") if isinstance(cam.get("pitch"), (int, float)) else cam.get("pitchDeg"),
            "style": state.get("style"), "layers": layers}


def go_home(session_id: str | None = None, live=None) -> dict:
    live = live or live_call
    v = home.get_or_default()
    fly = live("fly_to_location", {"latitude": v["lat"], "longitude": v["lon"],
                                    "rangeM": max(100, min(20_000_000, v["altitude_m"])), "waitForArrival": True}, session_id)
    if not fly.get("ok"):
        return {"ok": False, "home": v, "error": fly.get("error"), "message": fly.get("message")}
    live("set_visual_style", {"style": v["style"]}, session_id)
    for layer in v["layers"]:
        live("set_layer_visibility", {"layerId": layer, "enabled": True}, session_id)
    return {"ok": True, "home": v, "pane": fly.get("pane")}


def set_home(args: dict, session_id: str | None = None, live=None) -> dict:
    live = live or live_call
    args = dict(args or {})
    if args.get("lat") is None or args.get("lon") is None:  # take the current live view
        st = live("get_current_view_state", {}, session_id)
        if not st.get("ok"):
            return {"ok": False, "error": st.get("error"), "message": st.get("message") or
                    "Give lat and lon, or open the Eye Of Hermes (EOH) pane to save the current view."}
        cur = view_from_state(_structured_live(st))
        args = {**cur, **{k: v for k, v in args.items() if v not in (None, "", [])}}
    try:
        return {"ok": True, "home": home.set(args)}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"invalid home view: {exc}"}


# -- share links ---------------------------------------------------------------------------------
def long_link(base: str, v: dict) -> str:
    q = (f"v=2&lat={v['lat']:.4f}&lon={v['lon']:.4f}&alt={round(v['alt'])}"
         f"&heading={round(v.get('heading') or 0)}&pitch={round(v.get('pitch') if v.get('pitch') is not None else -90)}&roll=0")
    if v.get("style"):
        q += f"&style={v['style']}"
    return base.rstrip("/") + "/#" + q


def share_link(session_id: str | None = None, live=None, encoder=None) -> dict:
    live = live or live_call
    st = live("get_current_view_state", {}, session_id)
    if not st.get("ok"):
        return {"ok": False, "error": st.get("error"), "message": st.get("message")}
    cur = view_from_state(_structured_live(st))
    if not isinstance(cur.get("lat"), (int, float)):
        return {"ok": False, "error": "no camera in the current view state"}
    v = {"lat": cur["lat"], "lon": cur["lon"], "alt": cur.get("altitude_m") or 1_000_000,
         "heading": cur.get("heading"), "pitch": cur.get("pitch"), "style": cur.get("style"),
         "layers": cur.get("layers"), "base": config.public_url()}
    try:
        out = (encoder or encode_share)(v)
        return {"ok": True, "url": out["url"], "method": "share-code", "view": {k: v[k] for k in ("lat", "lon", "alt", "style", "layers")}}
    except Exception as exc:
        return {"ok": True, "url": long_link(v["base"], v), "method": "camera-hash",
                "note": f"share codec unavailable ({type(exc).__name__}); built from the camera state"}


def encode_share(v: dict) -> dict:
    node, root = config.node_bin(), config.checkout()
    if not node or not root:
        raise RuntimeError("checkout or node missing")
    out = subprocess.run([node, str(_SHARE_SCRIPT), str(root)], input=json.dumps(v), capture_output=True,
                         text=True, timeout=20, check=True)
    return json.loads(out.stdout)


# -- doctor --------------------------------------------------------------------------------------
def doctor() -> dict:
    checks = []

    def add(name, ok, detail, fix=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, **({"fix": fix} if not ok and fix else {})})

    node = config.node_bin()
    ver = ""
    if node:
        try:
            ver = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            ver = ""
    major = int(re.sub(r"\D.*", "", ver.lstrip("v")) or 0) if ver else 0
    add("node", major >= 24, f"Node {ver or 'not found'}",
        "Install Node 24 (e.g. into ~/.local/node24) or set GODSEYE_NODE to a Node 24 binary.")
    root = config.checkout()
    add("upstream checkout", root is not None, "found" if root else "not found",
        "Clone God's Eye View to ~/gods-eye-view (or set GODSEYE_HOME) and run `npm install` there.")
    up = False
    try:
        with urllib.request.urlopen(config.api_base() + "/", timeout=3) as r:
            up = r.status == 200
    except Exception:
        pass
    add("app reachable", up, f"{config.api_base()} {'answers' if up else 'not answering'}",
        "Start it: `cd ~/gods-eye-view && npm run dev` (port 4173), or set GODSEYE_API_BASE.")
    p = config.patched()
    add("patch #1571 HUD endpoint", p.get("hud"), "applied" if p.get("hud") else "missing",
        "Apply patches/0001-hud-summary-compatible-endpoint.patch to the checkout (git apply).")
    add("patch #1572 embed full UI", p.get("fullUi"), "applied" if p.get("fullUi") else "missing",
        "Apply patches/0002-embed-full-ui.patch to the checkout; without it the pane shows the globe only.")
    st = relay_status()
    relay_ok = "error" not in st
    add("live relay", relay_ok, "running" if relay_ok else (st.get("message") or "not running"),
        "Restart the Hermes dashboard/gateway with the godseye plugin enabled (the relay runs in its backend).")
    panes = [x for x in st.get("panes", []) if x.get("open")]
    age = min((x["lastPollAgeS"] for x in panes), default=None)
    add("pane connected", bool(panes), f"{len(panes)} open, last poll {age}s ago" if panes else "no pane polling",
        "Open the Eye Of Hermes (EOH) pane in Hermes Desktop or the Webapp and keep it visible.")
    for c in panel_checks(started=service_started_at()):
        checks.append({k: v for k, v in c.items() if k != "fix" or not c["ok"]})
    # Server-side provider keys (never baked into the panel; presence only, values never returned).
    for c in provider_key_checks():
        checks.append({k: v for k, v in c.items() if k != "fix" or not c["ok"]})
    if panes:
        info = pane_scene_info()
        r = info if info.get("action") == "hermes_scene_info" else (info.get("result") or info)
        if not isinstance(r, dict):
            r = {}
        ms = r.get("mapStack") or {}
        if ms:
            photoreal = next((x for x in ms.get("stacks", []) if x["id"] == "photoreal"), None)
            detail = f"active {ms.get('activeId')} ({ms.get('switchOrigin') or 'manual'})"
            if ms.get("lastError"):
                detail += f"; last fallback: {ms['lastError']}"
            baked = any(c["check"] == "key CESIUM_ION_TOKEN" and c["ok"] for c in checks)
            if photoreal is not None:
                detail += "; photoreal " + ("available" if photoreal["available"] else
                                            "unavailable: the baked Cesium ion token was refused (see last fallback)" if baked else
                                            "unavailable: no Cesium ion token in this bundle")
            add("map stack", not (photoreal is not None and not photoreal["available"]), detail,
                ("The token is in the bundle but ion/Google refused it: check it is valid and can read asset 2275207 "
                 "(Google Photorealistic 3D Tiles) at https://ion.cesium.com/tokens, then godseye_rebuild_panel.")
                if baked else MAP_STACK_FIX)
        else:
            add("map stack", True, "pane open; map stack not reported yet (open a view or retry)")
        # Layer feed states from the live view's feedProvenance (nominal/fallback/unavailable/…).
        for c in feed_state_checks(r):
            checks.append(c)
        try:
            sc = scene_op("list")
        except Exception:
            sc = {}
        if sc.get("ok"):
            n = len(sc.get("annotations") or [])
            add("annotations", True, f"{n} stored in the current scene; restored automatically on pane (re)open")
        pane_ann = r.get("annotations")
        if isinstance(pane_ann, int):
            add("pane annotations", True, f"{pane_ann} drawn in the open pane")
    else:
        try:
            sc = scene_op("list")
        except Exception:
            sc = {}
        if sc.get("ok"):
            add("annotations", True, f"{len(sc.get('annotations') or [])} stored in the default scene (no pane open)")
    add("public URL", True, "GODSEYE_PUBLIC_URL set" if config.public_url() != config.api_base()
        else "using the API base for links (set GODSEYE_PUBLIC_URL for links other devices can open)")
    from .gl_recover import MAX_FAILURES, WINDOW_MS, enabled as gl_recover_enabled
    if gl_recover_enabled():
        gl_detail = (
            f"on; a lost WebGL context recreates the viewer in the same pane and restores the saved scene. "
            f"Stops after {MAX_FAILURES} losses in {WINDOW_MS // 1000}s, then the click-to-reload card."
        )
    else:
        gl_detail = (
            "off (GODSEYE_GL_RECOVER or plugin-data/godseye/config.json glRecover); "
            "a lost context shows the click-to-reload card."
        )
    add("webgl recover", True, gl_detail)
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "panes": st.get("panes", [])}


def format_doctor(d: dict) -> str:
    lines = [("✅ " if d["ok"] else "⚠️ ") + "Eye Of Hermes (EOH) doctor"]
    for c in d["checks"]:
        code = f"[{c['code']}] " if c.get("code") else ""
        lines.append(f"{'✓' if c['ok'] else '✗'} {code}{c['check']}: {c['detail']}" + (f"\n    fix: {c['fix']}" if c.get("fix") and not c["ok"] else ""))
    return "\n".join(lines)


# -- /gev ----------------------------------------------------------------------------------------
HELP = ("/gev fly <place> · /gev layers [on|off <names>] · /gev status · /gev brief <place> · "
        "/gev home [set] · /gev share · /gev doctor · /gev research [light|full] · "
        "/gev save <name> · /gev list · /gev load <name> · /gev delete <name> --confirm · "
        "/gev report [name]")


def _msg(res: dict, ok: str) -> str:
    return ok if res.get("ok") else f"⚠️ {res.get('message') or res.get('error') or 'failed'}"


def gev_command(raw: str, live=None, mcp=None) -> str:
    live = live or live_call
    try:
        parts = shlex.split(raw or "")
    except ValueError:
        parts = (raw or "").split()
    if not parts or parts[0] in ("help", "-h", "--help"):
        return HELP
    cmd, rest = parts[0].lower(), parts[1:]
    if policy.refuse_reason({"q": " ".join(rest)}):
        return policy.refuse_reason({"q": " ".join(rest)})
    if cmd == "fly":
        if not rest:
            return "Usage: /gev fly <place>"
        place = " ".join(rest)
        return _msg(live("fly_to_location", {"query": place}), f"🌐 Flying to {place}.")
    if cmd == "layers":
        if rest and rest[0].lower() in ("on", "off"):
            on = rest[0].lower() == "on"
            done = []
            for name in rest[1:]:
                r = live("set_layer_visibility", {"layerId": name, "enabled": on})
                if not r.get("ok"):
                    return _msg(r, "") + f" ({name})"
                done.append(name)
            return f"Layers {'on' if on else 'off'}: {', '.join(done) or '(none given)'}"
        st = live("get_current_view_state", {})
        if not st.get("ok"):
            return _msg(st, "")
        layers = view_from_state(_structured_live(st))["layers"]
        return "Layers on: " + (", ".join(layers) or "none")
    if cmd == "status":
        st = relay_status()
        panes = [p for p in st.get("panes", []) if p.get("open")]
        lines = [f"Panes open: {len(panes)}"] + [
            f"  {p['id']}: last poll {p['lastPollAgeS']}s ago{' (busy)' if p.get('busy') else ''}" for p in panes]
        if panes:
            vs = live("get_current_view_state", {})
            if vs.get("ok"):
                v = view_from_state(_structured_live(vs))
                if isinstance(v.get("lat"), (int, float)):
                    lines.append(f"View: {v['lat']:.3f}, {v['lon']:.3f} at {round((v.get('altitude_m') or 0) / 1000)} km, "
                                 f"style {v.get('style') or 'normal'}, layers {', '.join(v['layers']) or 'none'}")
        else:
            lines.append(st.get("message") or "Open the Eye Of Hermes (EOH) pane to drive the live globe.")
        d = doctor()
        bad = [c["check"] for c in d["checks"] if not c["ok"]]
        lines.append("Health: all checks pass" if not bad else "Health: needs attention: " + ", ".join(bad) + " (run /gev doctor)")
        return "\n".join(lines)
    if cmd == "brief":
        if not rest:
            return "Usage: /gev brief <place>"
        out = brief_and_show({"place": " ".join(rest)}, live=live, mcp=mcp)
        if out.get("error"):
            return f"⚠️ {out['error']}"
        s = out["shown"]
        tail = (f"\n🌐 Shown on the globe: {len(s['layers'])} layers, {s['annotated']} marks."
                if s.get("globe_open") else f"\n{s.get('message', '')}")
        return out["brief"] + tail
    if cmd == "home":
        if rest and rest[0].lower() == "set":
            nums = []
            for x in rest[1:]:
                try:
                    nums.append(float(x))
                except ValueError:
                    pass
            args = dict(zip(("lat", "lon", "altitude_m"), nums))
            r = set_home(args, live=live)
            if r.get("ok"):
                h = r["home"]
                return f"🏠 Home saved: {h['lat']}, {h['lon']} at {round(h['altitude_m'] / 1000)} km, style {h['style']}."
            return _msg(r, "")
        r = go_home(live=live)
        return _msg(r, "🏠 Going home.")
    if cmd == "share":
        r = share_link(live=live)
        return r["url"] if r.get("ok") else _msg(r, "")
    if cmd == "doctor":
        return format_doctor(doctor())
    if cmd == "research":
        mode = (rest[0].lower() if rest and rest[0].lower() in ("light", "full") else "full")
        out = research_pins_op({"mode": mode, "all": True}, session_id=None)
        if not out.get("ok"):
            return f"⚠️ {out.get('message') or out.get('error') or 'research failed'}"
        return f"🔎 Researched {out.get('updated', 0)}/{out.get('researched', 0)} pins (mode={mode})."
    if cmd == "save":
        if not rest:
            return "Usage: /gev save <name>"
        out = investigation_op("save", None, args={"name": " ".join(rest)})
        return (f"💾 Saved investigation {out['name']} → {out['path']}" if out.get("ok")
                else f"⚠️ {out.get('message') or out.get('error')}")
    if cmd == "list":
        out = investigation_op("list", None, args={})
        if not out.get("ok"):
            return f"⚠️ {out.get('error')}"
        if not out.get("investigations"):
            return "No saved investigations."
        lines = [f"📚 {out['count']} investigation(s):"]
        for it in out["investigations"]:
            lines.append(f"  · {it['name']} — {it.get('pins', 0)} pins, {it.get('routes', 0)} routes")
        return "\n".join(lines)
    if cmd == "load":
        if not rest:
            return "Usage: /gev load <name>"
        out = investigation_op("load", None, args={"name": " ".join(rest), "restore": True})
        return (f"📂 Loaded {out.get('name')} ({out.get('pins', 0)} pins)." if out.get("ok")
                else f"⚠️ {out.get('message') or out.get('error')}")
    if cmd == "delete":
        confirm = False
        parts = list(rest)
        if parts and parts[-1] in ("--confirm", "confirm", "--yes"):
            confirm = True
            parts = parts[:-1]
        if not parts:
            return "Usage: /gev delete <name> --confirm"
        out = investigation_op("delete", None, args={"name": " ".join(parts), "confirm": confirm})
        return (f"🗑️ Deleted {out.get('deleted')}." if out.get("ok")
                else f"⚠️ {out.get('message') or out.get('error')}")
    if cmd == "report":
        rargs = {"capture": True}
        if rest:
            rargs["name"] = " ".join(rest)
        out = investigation_op("report", None, args=rargs)
        if not out.get("ok"):
            return f"⚠️ {out.get('message') or out.get('error')}"
        return f"📄 Report:\n  HTML: {out['html']}\n  Markdown: {out['markdown']}"
    return f"Unknown subcommand '{cmd}'. " + HELP


# -- v0.2 P0: scene, tour, panel freshness ---------------------------------------------------------
CLIENT_KEYS = {  # baked into dist/panel at build time (build/vite.js define)
    "CESIUM_ION_TOKEN": "Cesium ion photorealistic 3D / terrain (map stack 'photoreal')",
    "GOOGLE_MAPS_API_KEY": "Google basemaps, Places labels for the HUD",
    "MAPILLARY_CLIENT_TOKEN": "Mapillary street-level imagery",
}
# Server-only keys: checked for presence in the env file / process env; never baked, never printed.
SERVER_KEYS = {
    "AISSTREAM_API_KEY": "AISStream live vessels",
    "FIRMS_MAP_KEY": "NASA FIRMS active fires (also accepted as FIRMS_API_KEY)",
}
SERVER_KEY_ALIASES = {"FIRMS_MAP_KEY": ("FIRMS_API_KEY", "FIRMS_KEY", "NASA_FIRMS_KEY")}
SAFE_ION_SCOPES = {"assets:read", "assets:list"}
FEED_OK = {"nominal", "partial", "off", None}
FEED_WARN = {"fallback", "degraded", "stale", "loading"}
FEED_BAD = {"unavailable"}


def relay_post(path: str, body: dict, timeout: float = 600) -> dict:
    from . import live
    return live.call(path, _token(), body, timeout)


def scene_op(op: str, session_id: str | None = None, **extra) -> dict:
    body = {"op": op, **({"session_id": str(session_id)} if session_id else {}), **extra}
    return relay_post("/scene", body)


def tour_op(op: str, session_id: str | None = None, **extra) -> dict:
    body = {"op": op, **({"session_id": str(session_id)} if session_id else {}), **extra}
    return relay_post("/tour", body, 30)


def env_file() -> Path:
    import os
    raw = os.environ.get("GODSEYE_ENV_FILE", "")
    return Path(raw).expanduser() if raw else Path.home() / ".config" / "gods-eye-view.env"


def read_env(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k] = v
    except OSError:
        pass
    return out


def panel_bundle(root: Path | None = None) -> Path | None:
    root = root or config.checkout()
    if not root:
        return None
    files = sorted((root / "dist" / "panel" / "assets").glob("index-*.js"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def ion_scopes(token: str) -> list[str] | None:
    """Scopes claimed by a Cesium ion JWT, decoded locally (no network, never printed)."""
    import base64
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        return None
    scopes = data.get("scopes", data.get("scope"))
    if isinstance(scopes, str):
        scopes = scopes.replace(",", " ").split()
    return [str(s) for s in scopes] if isinstance(scopes, list) else None


def service_started_at(unit: str = "gods-eye-view.service") -> float | None:
    try:
        out = subprocess.run(["systemctl", "--user", "show", "-p", "ActiveEnterTimestamp", "--value", unit],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if not out or out == "n/a":
            return None
        from email.utils import parsedate_to_datetime  # noqa: F401 - format is "Thu 2026-11-05 09:53:27 EST"
        import datetime as dt
        parts = out.split()
        stamp = dt.datetime.strptime(" ".join(parts[1:3]), "%Y-%m-%d %H:%M:%S")
        return time.mktime(stamp.timetuple())
    except Exception:
        return None


def _env_has(file_env: dict, key: str) -> tuple[bool, str]:
    """Return (present, where-label). Values are never returned."""
    import os
    aliases = SERVER_KEY_ALIASES.get(key, ())
    for k in (key, *aliases):
        if file_env.get(k):
            return True, str(env_file())
        if os.environ.get(k):
            return True, "process env"
    return False, str(env_file())


def provider_key_checks(envf: Path | None = None) -> list[dict]:
    """Presence of server-side provider keys in the env file / process env. Never prints values."""
    envf = envf or env_file()
    file_env = read_env(envf)
    out = []
    for key, what in SERVER_KEYS.items():
        ok, where = _env_has(file_env, key)
        out.append({"check": f"key {key}", "ok": ok, **({} if ok else {"code": "KEY_MISSING"}),
                    "detail": (f"set in {where} ({what})" if ok else f"not set in {where} ({what})"),
                    "fix": f"Add {key}=... to {where} (chmod 600) and restart the God's Eye View app/service."})
    return out


def feed_state_checks(scene_or_view: dict | None) -> list[dict]:
    """Summarise layer feed states from feedProvenance on the open pane (nominal/fallback/unavailable)."""
    out = []
    src = scene_or_view or {}
    fp = src.get("feedProvenance") or (src.get("result") or {}).get("feedProvenance") or {}
    # Prefer feedProvenance already on the scene_info payload; avoid a second live round-trip here.
    if not fp:
        out.append({"check": "layer feeds", "ok": True,
                    "detail": "no feedProvenance on the open pane yet (enable a layer or wait for a refresh)"})
        return out
    overall = fp.get("overall")
    layers = fp.get("layers") or []
    by_state: dict[str, list[str]] = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        st = layer.get("feedState") or layer.get("state") or "unknown"
        name = str(layer.get("name") or layer.get("id") or "?")[:40]
        by_state.setdefault(str(st), []).append(name)
    parts = [f"{st}: {', '.join(names[:6])}" for st, names in sorted(by_state.items())]
    detail = f"overall {overall or 'unknown'}" + ("; " + "; ".join(parts) if parts else "")
    bad = overall in FEED_BAD or any(s in FEED_BAD for s in by_state)
    warn = overall in FEED_WARN or any(s in FEED_WARN for s in by_state)
    ok = not bad
    out.append({"check": "layer feeds", "ok": ok,
                **({"code": "FEED_UNAVAILABLE"} if bad else {"code": "FEED_DEGRADED"} if warn else {}),
                "detail": detail,
                **({"fix": "Check the provider key for the unavailable layer (godseye_doctor key checks) and the app's DATA_SOURCES.md."}
                   if not ok else {})})
    return out


def panes_op(prune: bool = False, label: str | None = None, pane_id: str | None = None,
             session_id: str | None = None) -> dict:
    body = {}
    if prune:
        body["prune"] = True
    if label is not None:
        body["label"] = label
    if pane_id:
        body["pane_id"] = pane_id
    if session_id:
        body["session_id"] = str(session_id)
    if not body:
        st = relay_status()
        return {"ok": True, "paneOpen": st.get("paneOpen"), "panes": st.get("panes") or [],
                **({} if "error" not in st else {"error": st.get("error"), "message": st.get("message")})}
    return relay_post("/panes", body, 10)


def panel_checks(root: Path | None = None, envf: Path | None = None, started: float | None = None) -> list[dict]:
    """PANEL_STALE / KEY_MISSING / ION_SCOPES findings. Values are compared, never returned."""
    from . import runtime_config
    root = root or config.checkout()
    envf = envf or env_file()
    out = []
    fix_build = "Run godseye_rebuild_panel (or: set -a; . %s; set +a; cd %s && PATH=~/.local/node24/bin:$PATH npm run build:panel), then godseye_live_reload_pane." % (envf, root or "~/gods-eye-view")
    fix_runtime = ("Open the Eye Of Hermes pane (or run godseye_rebuild_panel). A current God's Eye View build "
                   "reads dist/panel/config.json, which is rewritten from the env file when the pane is served, "
                   "so a key change does not need a rebuild. chmod 600. Never print the value. Older builds still "
                   "need the rebuild.")
    bundle = panel_bundle(root)
    if root is None:
        return out
    if bundle is None:
        out.append({"check": "panel bundle", "ok": False, "code": "PANEL_MISSING", "detail": "dist/panel is not built", "fix": fix_build})
        return out
    text = bundle.read_text(errors="ignore")
    file_env = read_env(envf)
    repo_env = read_env(root / ".env")
    runtime = runtime_config.supports_runtime(root, text)
    cfg_path = root / "dist" / "panel" / "config.json"
    cfg = runtime_config.read_config(cfg_path) if cfg_path.is_file() else {}
    if not cfg:
        pub = root / "public" / "config.json"
        if pub.is_file():
            cfg = runtime_config.read_config(pub)
            cfg_path = pub
    bmt = bundle.stat().st_mtime
    stale = []
    if runtime:
        drifted = False
        for key, _what in CLIENT_KEYS.items():
            val = file_env.get(key) or repo_env.get(key) or ""
            field = {"CESIUM_ION_TOKEN": "cesiumIonToken", "GOOGLE_MAPS_API_KEY": "googleMapsApiKey",
                     "MAPILLARY_CLIENT_TOKEN": "mapillaryToken"}[key]
            if val and cfg.get(field) != val and val not in text:
                drifted = True
        if drifted:
            stale.append("dist/panel/config.json does not match the env file")
    else:
        if envf.is_file() and envf.stat().st_mtime > bmt:
            stale.append(f"{envf} is newer than the panel bundle")
        if started and started > bmt and envf.is_file() and envf.stat().st_mtime > bmt:
            stale.append("the app service restarted with that env after the bundle was built")
    fix_fresh = fix_runtime if runtime else fix_build
    out.append({"check": "panel freshness", "ok": not stale, **({"code": "PANEL_STALE"} if stale else {}),
                "detail": "; ".join(stale) or (
                    "runtime config matches the env file" if runtime
                    else f"bundle built after {envf.name if envf.is_file() else 'env'} changed"),
                "fix": fix_fresh})
    for key, what in CLIENT_KEYS.items():
        val = file_env.get(key) or repo_env.get(key) or ""
        where = envf if key in file_env else (root / ".env" if key in repo_env else envf)
        field = {"CESIUM_ION_TOKEN": "cesiumIonToken", "GOOGLE_MAPS_API_KEY": "googleMapsApiKey",
                 "MAPILLARY_CLIENT_TOKEN": "mapillaryToken"}[key]
        if not val:
            ok = key != "CESIUM_ION_TOKEN"  # the one the photoreal stack hard-requires
            out.append({"check": f"key {key}", "ok": ok, **({} if ok else {"code": "KEY_MISSING"}),
                        "detail": f"not set in {where} ({what})",
                        "fix": f"Add {key}=... to {where} (chmod 600). Never print the value. "
                               + (fix_runtime if runtime else "Then run godseye_rebuild_panel.")})
            continue
        in_cfg = cfg.get(field) == val
        baked = val in text
        ok = (in_cfg or baked) if runtime else baked
        where_found = "dist/panel/config.json" if in_cfg else "the panel bundle"
        out.append({"check": f"key {key}", "ok": ok, **({} if ok else {"code": "KEY_MISSING"}),
                    "detail": f"set in {where}; {'present in ' + where_found if ok else 'not in config.json or the panel bundle'} ({what})",
                    "fix": fix_runtime if runtime else f"{key} is baked into dist/panel at build time. " + fix_build})
        if key == "CESIUM_ION_TOKEN":
            scopes = ion_scopes(val)
            extra = sorted(set(scopes or []) - SAFE_ION_SCOPES)
            if extra:
                out.append({"check": "Cesium ion token scopes", "ok": False, "code": "ION_SCOPES",
                            "detail": f"token is shipped to the browser but claims {', '.join(extra)}",
                            "fix": "Create a token limited to assets:read (and assets:list) at https://ion.cesium.com/tokens "
                                   "and put that one in the env file; see https://cesium.com/learn/ion/cesium-ion-access-tokens/"})
    return out


def pane_scene_info(session_id: str | None = None) -> dict:
    return live_call("hermes_scene_info", {}, session_id)


def rebuild_panel(session_id: str | None = None, runner=None, reload=True) -> dict:
    """npm run build:panel with the app's env file exported (Node 24), then reload open panes."""
    import os
    root, node = config.checkout(), config.node_bin()
    if not root or not node:
        return {"ok": False, "error": "God's Eye View checkout or Node 24 not found"}
    env = dict(os.environ)
    env.update({k: v for k, v in read_env(env_file()).items()})
    env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    t0 = time.time()
    run = runner or (lambda: subprocess.run(["npm", "run", "build:panel"], cwd=root, env=env, capture_output=True,
                                            text=True, timeout=900))
    try:
        proc = run()
    except Exception as exc:
        return {"ok": False, "error": f"build failed to run: {type(exc).__name__}"}
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-8:])
        for v in read_env(env_file()).values():  # never echo a key back
            if len(v) >= 8:
                tail = tail.replace(v, "***")
        return {"ok": False, "error": "build:panel failed", "log": tail[-1500:]}
    try:
        from . import runtime_config
        file_env = {**read_env(root / ".env"), **read_env(env_file())}
        for key in ("GOOGLE_MAPS_API_KEY", "CESIUM_ION_TOKEN", "MAPILLARY_CLIENT_TOKEN", "PHOTON_URL"):
            if env.get(key):
                file_env[key] = env[key]
        runtime_config.write_files(root, file_env)
    except Exception:
        pass
    out = {"ok": True, "builtS": round(time.time() - t0, 1),
           "checks": [{k: c[k] for k in ("check", "ok", "code", "detail") if k in c} for c in panel_checks(root)]}
    if reload:
        out["reload"] = live_call("hermes_reload", {}, session_id, fanout=True)
    return out


MAP_STACK_FIX = ("Put CESIUM_ION_TOKEN in ~/.config/gods-eye-view.env (or GODSEYE_ENV_FILE). "
                 "A current God's Eye View build reads dist/panel/config.json, rewritten from that file when the "
                 "pane is served, so a key change does not need a rebuild. Older builds compile the key into the "
                 "bundle: call godseye_rebuild_panel. godseye_doctor shows KEY_MISSING/PANEL_STALE. Never print the key.")


def remediate_map_stack(out: dict) -> dict:
    res = out.get("result") if isinstance(out.get("result"), dict) else out
    err = str((res or {}).get("error") or out.get("error") or "")
    if not (res or {}).get("ok", out.get("ok")) and re.search(r"token|key|ion", err, re.I):
        out = {**out, "remediation": MAP_STACK_FIX}
    return out


# -- research + investigations -----------------------------------------------------------------
def _relay_scenes():
    """In-process relay SceneStore when the relay runs in this process; else disk-backed store."""
    from . import live as live_mod
    from .scene import SceneStore, default_path
    rel = getattr(live_mod, "_relay", None)
    if rel is not None:
        return rel.scenes
    return SceneStore(default_path())


def research_pins_op(args: dict, session_id: str | None = None, live=None) -> dict:
    from . import research
    live = live or live_call
    scenes = _relay_scenes()
    key = str(session_id) if session_id else (scenes.pick([]) or "default")
    return research.research_pins(
        scenes, key,
        ids=args.get("ids"), labels=args.get("labels"),
        all_pins=bool(args.get("all", True)),
        agent_notes=args.get("agent_notes"),
        live=lambda n, a: live(n, a, session_id),
        persist=bool(args.get("persist", True)),
        mode=str(args.get("mode") or "full"),
    )


def investigation_op(op: str, session_id: str | None = None, args: dict | None = None, live=None) -> dict:
    from . import investigations
    args = dict(args or {})
    live = live or live_call
    scenes = _relay_scenes()
    key = str(session_id) if session_id else (scenes.pick([]) or "default")
    try:
        if op == "save":
            return investigations.save(scenes, key, args.get("name") or "",
                                       notes=args.get("notes"), views=args.get("views"),
                                       meta={"session": key})
        if op == "list":
            return investigations.list_all()
        if op == "load":
            restore = bool(args.get("restore", True))
            return investigations.load(
                scenes, key, args.get("name") or "",
                live=(lambda n, a: live(n, a, session_id)) if restore else None,
                restore=restore)
        if op == "delete":
            return investigations.delete(args.get("name") or "", confirm=bool(args.get("confirm")))
        if op == "report":
            share = share_link(session_id=session_id, live=live) if args.get("share") else None
            shot = note = None
            if args.get("capture", True):
                cap = investigations.capture_map(lambda n, a: live(n, a, session_id))
                if cap.get("ok"):
                    shot = cap["dataUrl"]
                else:
                    note = cap.get("note")
            return investigations.export_report(
                args.get("name"),
                scene_store=None if args.get("name") else scenes,
                session_key=key, screenshot_data_url=shot, screenshot_note=note, share=share)
        return {"ok": False, "error": "bad op"}
    except ValueError as exc:
        return {"ok": False, "error": "bad_args", "message": str(exc)}
