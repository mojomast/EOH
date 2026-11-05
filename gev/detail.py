"""Concise tool results by default.

Large-payload tools take ``detail: "concise" | "full"`` (concise when omitted).
Concise returns ids and counts and never inlines screenshots. ``full`` returns the
payload the tool already produced, including image bytes when the tool has them.

``detail`` is stripped before the argument dict is sent upstream: the MCP catalog and
the pane runner reject unknown properties.
"""
from __future__ import annotations

import json
import re

DETAIL_PROP = {
    "type": "string",
    "enum": ["concise", "full"],
    "default": "concise",
    "description": "concise (default) returns ids and counts and omits screenshots. "
                   "full returns the full objects and attaches images.",
}

# Data tools whose text or structured body is a list, a brief, or an image.
LARGE_DATA = {
    "get_earthquakes", "get_active_fires", "get_recent_launches",
    "aircraft_in_area", "find_aircraft", "get_aircraft_track", "get_aircraft_info",
    "vessels_in_area", "find_vessel", "get_vessel_track",
    "next_satellite_pass", "satellites_overhead",
    "find_cctv_cameras", "get_cctv_snapshot", "find_alpr_cameras", "get_traffic_flow",
    "get_weather_map", "get_recent_imagery",
    "find_submarine_cables", "find_infrastructure",
    "get_bhote_koshi_flood", "get_cyclones", "get_fire_perimeters",
    "find_military_installations", "get_map_features",
    "situation_brief", "military_awareness",
}

# Live-pane tools with the same shape. Small camera/layer commands are not listed,
# so their schemas stay free of an unused parameter.
LARGE_LIVE = {
    "get_entity_context", "get_current_view_state", "analyst_query", "resolve_area",
    "find_imagery", "osm_query", "next_iss_pass", "next_satellite_pass", "control_cctv",
    "annotate_map",
}

LARGE_EXTRA = {
    "godseye_brief_and_show", "godseye_find_places", "godseye_poi_details",
    "godseye_live_list_annotations",
    # Context / measure / research. Small numeric answers (one distance leg, a contour
    # summary) stay intact; geometry, catalogs and notes shrink.
    "godseye_live_context", "godseye_what_is_here", "godseye_measure",
    "godseye_isochrone", "godseye_live_draw", "godseye_live_compare",
    "godseye_research_pins",
    "godseye_investigation_list", "godseye_investigation_load", "godseye_investigation_report",
    "godseye_live_filter_pins",
    "godseye_watch_list", "godseye_watch_create",
}

IMAGE_TOOLS = {"get_cctv_snapshot", "get_weather_map", "get_recent_imagery", "find_imagery", "control_cctv"}

_ID_KEYS = ("id", "icao24", "mmsi", "noradId", "norad", "camera_id", "callsign", "name",
            "label", "osm_id", "place_id", "uuid", "areaId")
_KEEP = {"ok", "pane", "code", "message", "fix", "error", "detail", "hint", "fanout", "remediation"}
_BLOB_KEYS = {"image", "screenshot", "snapshot", "data", "base64", "png", "jpeg", "dataurl", "data_url"}
_GEOM_KEYS = {"geometry", "geom", "coordinates", "rings", "polygon", "positions", "line", "points"}
# Object lists at or under this serialized size stay (a distance leg, contour summaries,
# open/closed pin rows). Larger catalogs collapse to a count and ids.
_KEEP_LIST_BYTES = 2000
_B64 = re.compile(r"^[A-Za-z0-9+/=\s]+$")
CONCISE_CAP = 8000
FULL_CAP = 400_000
HINT = "Pass detail=full for full objects and screenshots."


def pop_detail(args: dict | None) -> str:
    """Remove ``detail`` from args. Absent or unknown values mean concise."""
    if not isinstance(args, dict) or "detail" not in args:
        return "concise"
    raw = args.pop("detail")
    return "full" if str(raw).strip().lower() == "full" else "concise"


def add_detail(schema: dict | None) -> dict:
    schema = json.loads(json.dumps(schema or {"type": "object", "properties": {}}))
    schema.setdefault("type", "object")
    props = schema.setdefault("properties", {})
    props["detail"] = dict(DETAIL_PROP)
    return schema


def _is_blob(value) -> bool:
    if not isinstance(value, str) or len(value) < 400:
        return False
    if value.startswith("data:image") or value.startswith("data:application"):
        return True
    sample = value[:240].strip()
    return len(value) > 800 and bool(_B64.fullmatch(sample))


def _blob_bytes(obj) -> int:
    if _is_blob(obj):
        return len(obj)
    if isinstance(obj, dict):
        return sum(_blob_bytes(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_blob_bytes(v) for v in obj)
    return 0


def _ident(obj: dict) -> str | None:
    for key in _ID_KEYS:
        val = obj.get(key)
        if isinstance(val, (str, int)) and not isinstance(val, bool) and str(val):
            return str(val)[:48]
    return None


def shrink_value(value, depth: int = 0):
    """Replace object lists with counts and ids, and image blobs with a byte note."""
    if _is_blob(value):
        return {"image": "omitted", "bytes": len(value)}
    if isinstance(value, str):
        return value if len(value) <= 400 else value[:400] + "…"
    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(x, dict) for x in value):
            if any(_blob_bytes(x) > 0 for x in value):
                return {"count": len(value), "images": "omitted", "bytes": sum(_blob_bytes(x) for x in value)}
            rows = value
            if any("enabled" in x or "visible" in x for x in value):
                rows = [x for x in value if x.get("enabled") is not False and x.get("visible") is not False]
                if not rows:
                    return {"count": 0, "ids": []}
            shrunk = [shrink_value(x, depth + 1) for x in rows]
            if len(json.dumps(shrunk, default=str)) <= _KEEP_LIST_BYTES:
                return shrunk
            ids = [i for i in (_ident(x) for x in rows) if i]
            out = {"count": len(rows), "ids": ids[:12]}
            if len(ids) > 12:
                out["more"] = len(ids) - 12
            return out
        if len(value) > 8:
            return {"count": len(value), "items": [shrink_value(x, depth + 1) for x in value[:8]]}
        return [shrink_value(x, depth + 1) for x in value]
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            low = str(key).lower()
            if low in _BLOB_KEYS and _is_blob(val):
                out[key] = {"image": "omitted", "bytes": len(val)}
                continue
            if low in _GEOM_KEYS and isinstance(val, (list, dict)):
                raw = json.dumps(val, default=str)
                if len(raw) > 180:
                    out[key] = {"omitted": key, "bytes": len(raw)}
                    continue
            out[key] = shrink_value(val, depth + 1)
        return out
    return value


def shrink_tool(payload: dict) -> tuple[dict, bool]:
    """Shrink a tool dict. Keeps error fields. Returns (payload, changed)."""
    if not isinstance(payload, dict):
        shrunk = shrink_value(payload)
        return {"value": shrunk}, shrunk != payload
    out = {}
    changed = False
    for key, val in payload.items():
        if key in _KEEP:
            out[key] = val
            continue
        low = str(key).lower()
        if low in _GEOM_KEYS and isinstance(val, (list, dict)):
            raw = json.dumps(val, default=str)
            if len(raw) > 180:
                out[key] = {"omitted": key, "bytes": len(raw)}
                changed = True
                continue
        nxt = shrink_value(val)
        if json.dumps(nxt, default=str, sort_keys=True) != json.dumps(val, default=str, sort_keys=True):
            changed = True
        out[key] = nxt
    if changed:
        out["detail"] = "concise"
        out["hint"] = HINT
    return out, changed


def _parse_json(text: str):
    text = (text or "").strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _text_and_images(result: dict) -> tuple[str, list[dict]]:
    content = (result or {}).get("content") or []
    parts, images = [], []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            parts.append(block["text"])
        elif block.get("type") in ("image", "image_url"):
            data = block.get("data") or (block.get("image_url") or {}).get("url") or ""
            if isinstance(data, str) and data:
                images.append({"mime": block.get("mimeType") or block.get("mime") or "application/octet-stream",
                               "bytes": len(data), "data": data})
    text = "\n".join(parts)
    return text, images


def render_mcp(name: str, result: dict, mode: str) -> str:
    """Model-facing string for one MCP tool result."""
    from .errors import annotate

    text, images = _text_and_images(result or {})
    if (result or {}).get("isError"):
        return json.dumps(annotate({"ok": False, "error": (text or "tool failed")[:2000]}, tool=name),
                          ensure_ascii=False)
    structured = (result or {}).get("structuredContent")
    if mode == "full":
        if images or (structured not in (None, {}, []) and _parse_json(text) is None and text):
            payload: dict = {}
            if text:
                payload["text"] = text
            if structured not in (None, {}, []):
                payload["structured"] = structured
            if images:
                packed = []
                for img in images:
                    # A sliced base64 string is not an image. Skip ones that cannot fit whole.
                    if len(img["data"]) > 350_000:
                        packed.append({"mime": img["mime"], "bytes": img["bytes"], "image": "too_large"})
                    else:
                        packed.append({"mime": img["mime"], "bytes": img["bytes"], "data": img["data"]})
                payload["images"] = packed
            raw = json.dumps(payload, ensure_ascii=False)
        elif not text and structured not in (None, {}, []):
            raw = json.dumps(structured, ensure_ascii=False)
        else:
            raw = text
        return raw if len(raw) <= FULL_CAP else raw[:FULL_CAP] + "…(truncated)"

    # concise: shrink a JSON body when that is what the model would have read;
    # keep a short prose summary (situation briefs) as-is so answers do not get worse.
    note = ""
    if images:
        note = f"[image omitted, {sum(i['bytes'] for i in images)} bytes; pass detail=full to attach]"
    parsed = _parse_json(text)
    if parsed is not None:
        body_obj = parsed if isinstance(parsed, dict) else {"items": parsed}
        shrunk, _changed = shrink_tool(body_obj)
        body = json.dumps(shrunk, ensure_ascii=False)
    elif (not text or len(text) > 700) and isinstance(structured, (dict, list)) and structured:
        src = structured if isinstance(structured, dict) else {"items": structured}
        shrunk, _changed = shrink_tool(src)
        if text and len(text) <= 700:
            shrunk = {"text": text, **{k: v for k, v in shrunk.items() if k not in ("text",)}}
        body = json.dumps(shrunk, ensure_ascii=False)
    elif len(text) > 700:
        body = text[:700] + "… [truncated; pass detail=full]"
    else:
        body = text
    if note:
        body = (body + ("\n" if body else "") + note).strip()
    if len(body) > CONCISE_CAP:
        body = body[:CONCISE_CAP] + "…"
    return body


def render_obj(payload: dict, mode: str) -> str:
    """Model-facing string for a live or extra tool dict. Failures stay intact (they carry fix)."""
    if mode != "full" and isinstance(payload, dict) and payload.get("ok") is not False:
        payload, _changed = shrink_tool(payload)
    text = json.dumps(payload, ensure_ascii=False)
    cap = FULL_CAP if mode == "full" else CONCISE_CAP
    return text if len(text) <= cap else text[:cap] + "…(truncated)"


def est_tokens(text: str) -> int:
    """Rough token count: UTF-8 bytes / 4. Not a model tokenizer."""
    return max(1, len(text.encode("utf-8")) // 4)


def measure(rows: list[tuple[str, str, str]]) -> list[dict]:
    """rows of (name, old_text, concise_text) plus an optional full via the caller.

    Each item is (name, old, concise, full). Returns byte and estimated-token deltas.
    """
    out = []
    for name, old, concise, full in rows:
        ob, cb, fb = len(old.encode()), len(concise.encode()), len(full.encode())
        out.append({
            "name": name,
            "old_bytes": ob,
            "concise_bytes": cb,
            "full_bytes": fb,
            "saved_bytes_vs_old": ob - cb,
            "saved_pct_vs_old": round(100 * (ob - cb) / ob, 1) if ob else 0.0,
            "saved_bytes_vs_full": fb - cb,
            "saved_pct_vs_full": round(100 * (fb - cb) / fb, 1) if fb else 0.0,
            "old_tokens_est": est_tokens(old),
            "concise_tokens_est": est_tokens(concise),
            "full_tokens_est": est_tokens(full),
        })
    return out
