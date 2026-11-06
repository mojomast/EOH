"""Update, batch delete, replace, and exact-duplicate skips for stored pins.

Identity for ``replace`` is a stable ``clientId`` or a coordinate within 25 m.
It is not the label text. Exact duplicates (same client id, or the same point
and the same label) are skipped unless ``replace`` asks for a patch.
"""
from __future__ import annotations

from .watch import haversine_km

REPLACE_M = 25.0
DUP_M = 1.0
_PATCH = ("label", "title", "description_md", "images", "links", "tags", "category", "clips")


def image_status(response_status, loaded: bool = False, error: str = "") -> str:
    """Pane image result: loaded, 404, csp-blocked, or error."""
    text = str(error or "").lower()
    if "content security policy" in text or text.strip() == "csp":
        return "csp-blocked"
    try:
        code = int(response_status)
    except (TypeError, ValueError):
        code = None
    if code == 0:
        return "csp-blocked"
    if code == 404:
        return "404"
    if loaded or code == 200:
        return "loaded"
    return "error"


def _client(spec: dict) -> str:
    return str(spec.get("clientId") or spec.get("id") or "")[:80]


def _point(spec: dict) -> tuple[float, float] | None:
    lat, lon = spec.get("latitude"), spec.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not isinstance(lat, bool):
        return float(lat), float(lon)
    return None


def _same_label(a: dict, b: dict) -> bool:
    left = str(a.get("label") or a.get("title") or "").strip().lower()
    right = str(b.get("label") or b.get("title") or "").strip().lower()
    return bool(left) and left == right


def _dist_m(a: dict, b: dict) -> float | None:
    pa, pb = _point(a), _point(b)
    if not pa or not pb:
        return None
    return haversine_km(pa[0], pa[1], pb[0], pb[1]) * 1000.0


def public_meta(spec: dict) -> dict:
    spec = spec or {}
    images = spec.get("images") if isinstance(spec.get("images"), list) else []
    links = spec.get("links") if isinstance(spec.get("links"), list) else []
    tags = spec.get("tags") if isinstance(spec.get("tags"), dict) else {}
    clips = spec.get("clips") if isinstance(spec.get("clips"), list) else []
    out = {
        "title": spec.get("title") or spec.get("label") or "",
        "description_md": spec.get("description_md") or "",
        "images": images[:6],
        "links": links[:12],
        "tags": tags,
    }
    if clips:
        out["clips"] = clips[:4]
    if spec.get("category"):
        out["category"] = spec.get("category")
    if _client(spec):
        out["clientId"] = _client(spec)
    return out


def classify(existing: list[dict], spec: dict, replace: bool) -> tuple[str, dict | None]:
    """Return ``(add|skip|update, stored record or None)``."""
    client = _client(spec)
    for ann in existing:
        stored = ann.get("spec") if isinstance(ann.get("spec"), dict) else ann
        same = client and (client == _client(stored) or ann.get("id") == client or ann.get("paneId") == client)
        if same:
            return ("update" if replace else "skip"), ann
    if replace:
        best, best_d = None, None
        for ann in existing:
            stored = ann.get("spec") if isinstance(ann.get("spec"), dict) else ann
            dist = _dist_m(stored, spec)
            if dist is None or dist > REPLACE_M:
                continue
            if best_d is None or dist < best_d:
                best, best_d = ann, dist
        if best is not None:
            return "update", best
    for ann in existing:
        stored = ann.get("spec") if isinstance(ann.get("spec"), dict) else ann
        dist = _dist_m(stored, spec)
        if dist is None or dist > DUP_M:
            continue
        if _same_label(stored, spec) and str(stored.get("type") or "pin") == str(spec.get("type") or "pin"):
            return "skip", ann
    return "add", None


def patch_fields(spec: dict) -> dict:
    out = {}
    for key in _PATCH:
        if key in spec and spec[key] is not None:
            out[key] = spec[key]
    if spec.get("clientId"):
        out["clientId"] = spec["clientId"]
    return out


def plan(existing: list[dict], annotations: list, replace: bool) -> dict:
    """Split a batch into pins to draw, pins to patch, and skips."""
    add, update, skipped = [], [], []
    have = list(existing or [])
    for spec in annotations or []:
        if not isinstance(spec, dict):
            continue
        kind, match = classify(have, spec, replace)
        if kind == "skip":
            skipped.append(_client(spec) or spec.get("label") or "")
            continue
        if kind == "update" and match is not None:
            update.append({"id": match.get("id"), "paneId": match.get("paneId"), "fields": patch_fields(spec)})
            stored = match.get("spec") if isinstance(match.get("spec"), dict) else None
            if isinstance(stored, dict):
                stored.update(patch_fields(spec))
            continue
        add.append(spec)
        have.append({"id": None, "spec": spec})
    return {"add": add, "update": update, "skipped": [item for item in skipped if item != ""]}
