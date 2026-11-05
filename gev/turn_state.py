"""One-line map state injected before each model turn.

Hermes plugins register ``pre_llm_call`` (see NousResearch/hermes-agent
``ctx.register_hook``). The callback returns ``{"context": "<line>"}``, which the
host appends to the current user message. The line is built from the relay's cached
pane snapshot (heartbeat or the last view-state result) plus the scene's pin count
and open drawer. It does not call the pane, so a busy globe is not queued behind a
state poll.

Set ``GODSEYE_TURN_STATE=0`` to disable. Hosts without ``register_hook`` skip this
and the tools keep working.
"""
from __future__ import annotations

import os
import re

ENV = "GODSEYE_TURN_STATE"
MAX_BYTES = 300
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,32}")
_SECRET = re.compile(r"AIza|eyJ|sk-|api[_-]?key|BEGIN |token", re.I)


def enabled() -> bool:
    return os.environ.get(ENV, "1").strip().lower() not in {"0", "false", "off", "no", ""}


def _num(value) -> str:
    value = round(float(value), 1)
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def _coord(value) -> str:
    return f"{float(value):.4f}"


def sanitize_snapshot(raw: dict | None) -> dict:
    """Keep numbers and short layer ids. Drop anything that looks like a key or URL."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in ("lat", "lon"):
        val = raw.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and abs(val) <= (90 if key == "lat" else 180):
            out[key] = round(float(val), 4)
    rng = raw.get("rangeM")
    if isinstance(rng, (int, float)) and not isinstance(rng, bool):
        out["rangeM"] = max(0, min(40_000_000, int(rng)))
    for key in ("pitch", "heading"):
        val = raw.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = round(float(val), 1)
    if isinstance(raw.get("layers"), list):
        ids = []
        for item in raw["layers"]:
            text = str(item)[:32]
            if _ID.fullmatch(text) and not _SECRET.search(text):
                ids.append(text)
            if len(ids) >= 12:
                break
        out["layers"] = ids
    pins = raw.get("pins")
    if isinstance(pins, int) and not isinstance(pins, bool):
        out["pins"] = max(0, min(500, pins))
    if "drawer" in raw:
        drawer = raw.get("drawer")
        if not drawer:
            out["drawer"] = None
        else:
            text = re.sub(r"\s+", " ", str(drawer)).strip()
            out["drawer"] = "open" if (not text or _SECRET.search(text) or len(text) > 80) else text[:40]
    return out


def absorb_result(name: str, args: dict | None, res: dict | None) -> dict:
    """Pull camera / layers out of a tool result so the next turn does not need a poll."""
    if not isinstance(res, dict) or res.get("ok") is False:
        return {}
    result = res.get("result") if isinstance(res.get("result"), dict) else res
    if isinstance(result, dict) and result.get("ok") is False:
        return {}
    cam = result.get("camera") if isinstance(result, dict) and isinstance(result.get("camera"), dict) else {}
    out: dict = {}
    lat = cam.get("targetLat", cam.get("latitude"))
    lon = cam.get("targetLon", cam.get("longitude"))
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        out["lat"] = round(float(lat), 4)
        out["lon"] = round(float(lon), 4)
    rng = cam.get("rangeM", cam.get("heightM"))
    if isinstance(rng, (int, float)) and not isinstance(rng, bool):
        out["rangeM"] = max(0, min(40_000_000, int(rng)))
    pitch = cam.get("pitchDeg", cam.get("pitch"))
    heading = cam.get("headingDeg", cam.get("heading"))
    if isinstance(pitch, (int, float)) and not isinstance(pitch, bool):
        out["pitch"] = round(float(pitch), 1)
    if isinstance(heading, (int, float)) and not isinstance(heading, bool):
        out["heading"] = round(float(heading), 1)
    layers = result.get("layers") if isinstance(result, dict) else None
    if isinstance(layers, list) and layers and name in ("get_current_view_state", "fly_to_location", "move_camera"):
        ids = []
        for layer in layers:
            if isinstance(layer, str):
                ids.append(layer)
            elif isinstance(layer, dict) and layer.get("enabled") is not False and layer.get("visible") is not False:
                ident = layer.get("id") or layer.get("layerId")
                if ident:
                    ids.append(str(ident))
        cleaned = sanitize_snapshot({"layers": ids}).get("layers")
        if cleaned:
            out["layers"] = cleaned
    return sanitize_snapshot(out) if out else {}


def format_line(status: str, snap: dict | None, pins: int | None, drawer: str | None) -> str:
    """Single line, at most 300 UTF-8 bytes."""
    status = status if status in ("connected", "busy", "closed") else "closed"
    if status == "closed":
        return "EOH pane=closed"
    snap = snap or {}
    layers = [str(x) for x in (snap.get("layers") or [])][:12]
    drawer_txt = ""
    if drawer:
        drawer_txt = re.sub(r"\s+", " ", str(drawer)).strip()[:40]

    def build(layers_now: list[str], drawer_now: str) -> str:
        parts = [f"EOH pane={status}"]
        if isinstance(snap.get("lat"), (int, float)) and isinstance(snap.get("lon"), (int, float)):
            parts.append(f"cam={_coord(snap['lat'])},{_coord(snap['lon'])}")
        if isinstance(snap.get("rangeM"), (int, float)):
            parts.append(f"r={int(snap['rangeM'])}m")
        if isinstance(snap.get("pitch"), (int, float)):
            parts.append(f"p={_num(snap['pitch'])}")
        if isinstance(snap.get("heading"), (int, float)):
            parts.append(f"h={_num(snap['heading'])}")
        if layers_now:
            parts.append("layers=" + ",".join(layers_now))
        if isinstance(pins, int):
            parts.append(f"pins={pins}")
        parts.append("drawer=" + (drawer_now or "none"))
        return " ".join(parts)

    line = build(layers, drawer_txt)
    while len(line.encode("utf-8")) > MAX_BYTES and layers:
        layers = layers[:-1]
        line = build(layers, drawer_txt)
    if len(line.encode("utf-8")) > MAX_BYTES and drawer_txt:
        # Keep the marker so the agent still knows a drawer is open.
        drawer_txt = "open"
        line = build(layers, drawer_txt)
    raw = line.encode("utf-8")
    if len(raw) > MAX_BYTES:
        line = raw[: MAX_BYTES - 1].decode("utf-8", "ignore").rstrip() + "…"
    return line


def line_from_parts(status: str, snap: dict | None, scene: dict | None) -> str:
    """Merge a pane snapshot with the scene store (camera fallback, pins, drawer)."""
    snap = dict(snap or {})
    scene = scene or {}
    cam = scene.get("camera") if isinstance(scene.get("camera"), dict) else None
    if cam and "lat" not in snap and isinstance(cam.get("lat"), (int, float)) and isinstance(cam.get("lon"), (int, float)):
        snap["lat"] = round(float(cam["lat"]), 4)
        snap["lon"] = round(float(cam["lon"]), 4)
        if "rangeM" not in snap and isinstance(cam.get("heightM"), (int, float)):
            snap["rangeM"] = int(cam["heightM"])
        if "pitch" not in snap and isinstance(cam.get("pitchDeg"), (int, float)):
            snap["pitch"] = cam["pitchDeg"]
        if "heading" not in snap and isinstance(cam.get("headingDeg"), (int, float)):
            snap["heading"] = cam["headingDeg"]
    pins = snap.get("pins") if isinstance(snap.get("pins"), int) else None
    if pins is None:
        pins = len(scene.get("annotations") or [])
    if "drawer" in snap:
        drawer = snap.get("drawer")
    else:
        stored = scene.get("drawer") if isinstance(scene.get("drawer"), dict) else None
        drawer = (stored or {}).get("label") if stored else None
    return format_line(status, snap, pins, drawer if isinstance(drawer, str) or drawer is None else str(drawer))


def fetch_line(session_id: str | None) -> str | None:
    """Read the cached line from the loopback relay. None when the relay is down."""
    try:
        from . import bridge, live
        token = bridge.ensure_token()
        out = live.call("/mapstate", token, {"session_id": session_id}, timeout=2.0)
    except Exception:
        return None
    if not isinstance(out, dict):
        return None
    line = out.get("line")
    return line if isinstance(line, str) and line.startswith("EOH ") else None


def pre_llm_call(session_id=None, user_message=None, conversation_history=None,
                 is_first_turn=None, model=None, platform=None, **_kwargs):
    """Hermes pre_llm_call hook. Return {"context": line} or None to inject nothing.

    Hermes calls this positionally (session_id, user_message, history, is_first_turn,
    model, platform). Keyword calls work too.
    """
    if not enabled():
        return None
    try:
        line = fetch_line(session_id)
    except Exception:
        return None
    if not line or len(line.encode("utf-8")) > MAX_BYTES:
        return None
    return {"context": line}


def register(ctx) -> bool:
    """Register the hook when this Hermes build has one and the switch is on."""
    if not enabled() or ctx is None:
        return False
    hook = getattr(ctx, "register_hook", None)
    if not callable(hook):
        return False
    try:
        hook("pre_llm_call", pre_llm_call)
    except Exception:
        return False
    return True
