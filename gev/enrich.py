"""Always-on pin drawer enrichment: every pin gets non-empty content tailored to the user's intent.

Light mode (default): uses facts already on the pin / POI / brief hit, shaped by ``intent``.
Full mode: delegates to ``research.research_one`` (Wikipedia/Wikidata/Commons/website meta).

If a pin is still empty after creation, ``schedule_auto_enrich`` researches it in a background
thread and pushes an annotate_map update to the open pane.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable

# Intent keywords -> preferred fact keys (order = display order).
INTENT_FACTS = [
    (re.compile(r"ramen|sushi|noodle|restaurant|cafe|bar|food|menu|cuisine|eat|dining", re.I),
     ("cuisine", "opening_hours", "hours", "price", "prices", "menu", "website", "phone", "address")),
    (re.compile(r"hotel|lodging|stay|airbnb|hostel", re.I),
     ("stars", "opening_hours", "phone", "website", "address", "cuisine")),
    (re.compile(r"fire|firms|burn|wildfire|smoke", re.I),
     ("detection_time", "acq_date", "acq_time", "confidence", "frp", "brightness", "source", "satellite")),
    (re.compile(r"quake|earthquake|seismic|tremor", re.I),
     ("magnitude", "mag", "depth_km", "depth", "time", "place", "source")),
    (re.compile(r"ship|vessel|ais|boat|tanker|cargo", re.I),
     ("mmsi", "shipname", "ship_type", "type", "speed", "course", "destination", "flag", "source")),
    (re.compile(r"flight|aircraft|plane|ads-?b|airport", re.I),
     ("callsign", "icao24", "origin_country", "altitude", "velocity", "squawk", "source")),
    (re.compile(r"military|base|installation|naval|airbase", re.I),
     ("type", "operator", "source", "distance_km", "address")),
    (re.compile(r"museum|landmark|monument|park|tour|visit|history", re.I),
     ("opening_hours", "website", "address", "phone", "wikipedia", "wikidata")),
]

FACT_LABELS = {
    "cuisine": "Cuisine", "opening_hours": "Hours", "hours": "Hours", "price": "Price",
    "prices": "Price", "menu": "Menu", "website": "Website", "phone": "Phone", "address": "Address",
    "detection_time": "Detected", "acq_date": "Date", "acq_time": "Time", "confidence": "Confidence",
    "frp": "FRP", "brightness": "Brightness", "source": "Source", "satellite": "Satellite",
    "magnitude": "Magnitude", "mag": "Magnitude", "depth_km": "Depth (km)", "depth": "Depth",
    "time": "Time", "place": "Place", "mmsi": "MMSI", "shipname": "Name", "ship_type": "Type",
    "type": "Type", "speed": "Speed", "course": "Course", "destination": "Destination", "flag": "Flag",
    "callsign": "Callsign", "icao24": "ICAO24", "origin_country": "Country", "altitude": "Altitude",
    "velocity": "Velocity", "squawk": "Squawk", "operator": "Operator", "distance_km": "Distance (km)",
    "wikipedia": "Wikipedia", "wikidata": "Wikidata", "stars": "Stars", "section": "Section",
    "label": "Label", "name": "Name",
}

DRAWER_KEYS = ("title", "description_md", "images", "links")
PIN_TYPES = {"pin", "marker", "point", "highlight", "card"}


def _md(s) -> str:
    return re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", str(s))


def _http_url(u) -> str | None:
    u = str(u or "").strip()
    if u and not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I) and re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", u):
        u = "https://" + u
    return u if re.match(r"^https?://[^\s<>\"']+$", u, re.I) else None


def preferred_keys(intent: str | None) -> tuple[str, ...]:
    text = str(intent or "")
    for rx, keys in INTENT_FACTS:
        if rx.search(text):
            return keys
    return ("address", "opening_hours", "phone", "website", "cuisine", "source",
            "distance_km", "type", "section")


def has_drawer(spec: dict | None) -> bool:
    if not isinstance(spec, dict):
        return False
    md = str(spec.get("description_md") or "").strip()
    imgs = spec.get("images") or []
    links = spec.get("links") or []
    return bool(md) or bool(imgs) or bool(links)


def _pick_facts(src: dict, keys: tuple[str, ...]) -> list[tuple[str, str]]:
    out = []
    seen = set()
    for k in keys:
        if k in seen:
            continue
        v = src.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, float):
            v = f"{v:.2f}" if abs(v) < 1000 else str(round(v))
        elif isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v[:6])
        else:
            v = str(v).strip()
        if not v:
            continue
        seen.add(k)
        out.append((FACT_LABELS.get(k, k.replace("_", " ").title()), v))
    return out


def light_drawer(spec: dict, intent: str | None = None, extra: dict | None = None) -> dict:
    """Build non-empty drawer fields from the pin/POI/hit itself, shaped by intent. No network."""
    src = {}
    src.update(extra or {})
    src.update({k: v for k, v in (spec or {}).items() if v is not None})
    # flatten nested row/hit payloads
    for nest in ("tags", "properties", "data", "row"):
        if isinstance(src.get(nest), dict):
            for k, v in src[nest].items():
                src.setdefault(k, v)
    label = str(spec.get("title") or spec.get("label") or spec.get("target") or src.get("name")
                or src.get("callsign") or "Pin")[:120]
    keys = preferred_keys(intent)
    facts = _pick_facts(src, keys)
    # Always include coordinates when present
    lat, lon = spec.get("latitude"), spec.get("longitude")
    lines = []
    if intent and not str(spec.get("description_md") or "").strip():
        lines.append(f"_Re: {_md(str(intent)[:160])}_")
    existing_md = str(spec.get("description_md") or "")
    for lab, val in facts[:8]:
        if lab.lower() == "type" and str(val).lower() in ("pin", "marker", "point", "highlight", "card"):
            continue
        if lab.lower() in existing_md.lower():
            continue
        if lab.lower() in ("website", "menu") and _http_url(val):
            lines.append(f"**{lab}:** [{_md(val)}]({_http_url(val)})")
        elif lab.lower() == "hours":
            lines.append(f"**{lab}:** `{val.replace('`', '')}`")
        else:
            lines.append(f"**{lab}:** {_md(val)}")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and "position" not in existing_md.lower():
        lines.append(f"**Position:** `{float(lat):.5f}, {float(lon):.5f}`")
    if not facts and not lines:
        # absolute fallback — never leave empty
        why = f"Marked for your request" + (f" (“{_md(str(intent)[:80])}”)" if intent else "") + "."
        lines = [why, "Further details unavailable from local tags."]
    links = []
    for existing in spec.get("links") or []:
        if isinstance(existing, dict) and _http_url(existing.get("url")):
            links.append({"url": _http_url(existing["url"]),
                          "label": str(existing.get("label") or existing["url"])[:80]})
    for key, lab in (("website", "Website"), ("menu", "Menu"), ("url", "Link")):
        u = _http_url(src.get(key))
        if u and not any(l["url"] == u for l in links):
            links.append({"url": u, "label": lab})
    images = []
    for existing in spec.get("images") or []:
        if isinstance(existing, dict) and _http_url(existing.get("url")):
            images.append({"url": _http_url(existing["url"]),
                           "alt": str(existing.get("alt") or label)[:120],
                           **({"caption": existing["caption"]} if existing.get("caption") else {})})
    md = str(spec.get("description_md") or "").strip()
    if md:
        # keep agent-authored body; append missing intent facts that aren't already mentioned
        extra_lines = [ln for ln in lines if ln.startswith("**") and ln.split(":**")[0][2:].lower() not in md.lower()]
        description_md = md if not extra_lines else (md + "\n\n" + "\n\n".join(extra_lines[:4]))
    else:
        description_md = "\n\n".join(lines)[:4000]
    out = {"title": str(spec.get("title") or label)[:120],
           "description_md": description_md[:4000],
           "links": links[:12], "images": images[:6]}
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def ensure_drawer(spec: dict, intent: str | None = None, mode: str = "light",
                  extra: dict | None = None) -> dict:
    """Return a copy of ``spec`` guaranteed to have non-empty drawer content."""
    if not isinstance(spec, dict):
        return spec
    out = dict(spec)
    t = str(out.get("type") or "pin").lower()
    if t not in PIN_TYPES and t != "card":
        return out
    if mode == "full" and not has_drawer(out):
        try:
            from . import research
            full = research.research_one(out, budget=[2])  # light-full: capped
            if full.get("ok") and full.get("description_md"):
                for k in DRAWER_KEYS:
                    if full.get(k):
                        out[k] = full[k]
        except Exception:
            pass
    if not has_drawer(out) or intent:
        filled = light_drawer(out, intent=intent, extra=extra)
        for k, v in filled.items():
            if k == "description_md" and has_drawer(out) and not intent:
                continue
            if k == "description_md" and out.get("description_md") and intent:
                # merge intent-shaped facts without wiping agent text
                out[k] = filled["description_md"]
            elif k not in out or not out.get(k):
                out[k] = v
            elif k in ("links", "images") and filled.get(k):
                # merge unique
                have = {x.get("url") for x in (out.get(k) or []) if isinstance(x, dict)}
                merged = list(out.get(k) or [])
                for x in filled[k]:
                    if x.get("url") not in have:
                        merged.append(x)
                        have.add(x.get("url"))
                out[k] = merged
    if not has_drawer(out):
        # last resort
        out["description_md"] = light_drawer(out, intent=intent, extra=extra)["description_md"]
        out.setdefault("title", str(out.get("label") or "Pin")[:120])
    return out


def enrich_annotations(annotations: list, intent: str | None = None, mode: str = "light") -> list:
    """Enrich every pin/card in an annotate_map annotations list."""
    out = []
    for a in annotations or []:
        if isinstance(a, dict):
            out.append(ensure_drawer(a, intent=intent, mode=mode))
        else:
            out.append(a)
    return out


# -- background auto-enrich after a pin lands without content ------------------------------------
_bg_lock = threading.Lock()
_bg_pending: set[str] = set()


def schedule_auto_enrich(scene_store, session_key: str, ann_id: str, spec: dict,
                         intent: str | None = None, live: Callable | None = None,
                         mode: str = "light") -> None:
    """If ``spec`` lacks drawer content, research it in a daemon thread and push to the pane."""
    if has_drawer(spec):
        return
    key = f"{session_key}:{ann_id}"
    with _bg_lock:
        if key in _bg_pending:
            return
        _bg_pending.add(key)

    def work():
        try:
            filled = ensure_drawer(dict(spec), intent=intent, mode="full" if mode == "full" else "light")
            if mode != "full":
                # still try a single cached wikipedia hit when we have a label
                try:
                    from . import research
                    one = research.research_one(filled, budget=[2])
                    if one.get("ok") and one.get("description_md"):
                        for k in DRAWER_KEYS:
                            if one.get(k):
                                filled[k] = one[k]
                except Exception:
                    pass
            if not has_drawer(filled):
                return
            patch = {k: filled[k] for k in DRAWER_KEYS if filled.get(k) is not None}
            if not scene_store.update_annotation(session_key, ann_id, patch):
                return
            if live is None:
                return
            # push update: clear+redraw is heavy; instead re-annotate this one pin with coords
            pin = {"type": filled.get("type") or "pin",
                   "latitude": filled.get("latitude"), "longitude": filled.get("longitude"),
                   "label": filled.get("label") or filled.get("title"),
                   **patch}
            if isinstance(pin.get("latitude"), (int, float)):
                try:
                    live("annotate_map", {"annotations": [pin], "persist": True})
                except Exception:
                    pass
        finally:
            with _bg_lock:
                _bg_pending.discard(key)

    threading.Thread(target=work, name=f"eoh-enrich-{ann_id}", daemon=True).start()


def after_annotate(scene_store, session_key: str, args: dict, result: dict,
                   intent: str | None = None, live: Callable | None = None) -> None:
    """Called after annotate_map is recorded: queue background enrich for empty drawers."""
    items = result.get("items") if isinstance(result.get("items"), list) else []
    specs = (args or {}).get("annotations") or []
    intent = intent or (args or {}).get("intent") or (args or {}).get("context")
    for i, spec in enumerate(specs):
        if not isinstance(spec, dict):
            continue
        if has_drawer(spec):
            continue
        got = items[i] if i < len(items) and isinstance(items[i], dict) else {}
        # find the scene annotation id we just stored (match by coords)
        lat, lon = got.get("latitude", spec.get("latitude")), got.get("longitude", spec.get("longitude"))
        with scene_store.lock:
            sc = scene_store.scene(session_key)
            match = None
            for a in reversed(sc.get("annotations") or []):
                s = a.get("spec") or {}
                if s.get("latitude") == lat and s.get("longitude") == lon:
                    match = a
                    break
                if got.get("id") and a.get("paneId") == got.get("id"):
                    match = a
                    break
        if match:
            schedule_auto_enrich(scene_store, session_key, match["id"], match["spec"],
                                 intent=intent, live=live)
