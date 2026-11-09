"""Nearest street-level panorama for one coordinate.

Google Street View metadata is tried first. Mapillary is the fallback when
that lookup misses or the Google key is unset. Keys are read only through
Hermes ``agent.secret_scope.get_secret`` (catalog rule 11) and are never
copied into a result, an error, or a log line. The pane still opens the
picture: this module only names the panorama.
"""
from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request

from .measure import haversine_m
from .poi import USER_AGENT

GOOGLE_META = "https://maps.googleapis.com/maps/api/streetview/metadata"
MAPILLARY_IMAGES = "https://graph.mapillary.com/images"
LOOKUP_RADIUS_M = 50
_SECRET = re.compile(r"(key|access_token|token|client_token)=([^&\s]+)", re.I)
_BEARER = re.compile(r"Bearer\s+\S+", re.I)

_HERMES_KEY_FIX = (
    "Set GOOGLE_MAPS_API_KEY and/or MAPILLARY_CLIENT_TOKEN in Hermes "
    "(``hermes plugins install`` prompts, or save them with Hermes into the "
    "active profile ``.env``), then retry. Do not print the key. Do not put "
    "keys in a Gods Eye View env file for this plugin."
)


def _clean(text: str) -> str:
    text = _SECRET.sub(lambda m: f"{m.group(1)}=REDACTED", str(text or ""))
    return _BEARER.sub("Bearer REDACTED", text)


def _get(url: str, headers: dict | None = None, timeout: float = 8) -> tuple[int, str]:
    """GET that never raises and never returns a secret-bearing URL."""
    from .public_http import UnsafeURL, header_value, request as public_request
    try:
        return public_request(
            url, timeout=timeout, limit=200_000,
            headers={"User-Agent": header_value(USER_AGENT), "Accept": "application/json", **(headers or {})},
        )
    except (UnsafeURL, Exception):
        return 0, ""


def _secret(name: str) -> str:
    """Profile-scoped credential via Hermes. Never reads credential files or process env directly."""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return ""
    try:
        value = get_secret(name)
    except Exception:
        # UnscopedSecretError (multiplex) or any host error: degrade, never crash.
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _google_key() -> str:
    return _secret("GOOGLE_MAPS_API_KEY") or _secret("GOOGLE_API_KEY") or ""


def _mapillary_token() -> str:
    return _secret("MAPILLARY_CLIENT_TOKEN") or ""


def _bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 111_320
    scale = max(0.2, math.cos(math.radians(lat)))
    dlon = radius_m / (111_320 * scale)
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def _google(lat: float, lon: float, radius_m: float) -> dict | None:
    key = _google_key()
    if not key:
        return None
    query = urllib.parse.urlencode({
        "location": f"{lat:.6f},{lon:.6f}",
        "radius": int(max(1, min(50, radius_m))),
        "source": "outdoor",
        "key": key,
    })
    status, body = _get(GOOGLE_META + "?" + query)
    if status != 200 or not body:
        return None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    if payload.get("status") != "OK" or not payload.get("pano_id"):
        return None
    loc = payload.get("location") if isinstance(payload.get("location"), dict) else {}
    try:
        plat, plon = float(loc.get("lat")), float(loc.get("lng"))
    except (TypeError, ValueError):
        plat, plon = lat, lon
    return {
        "ok": True, "provider": "google", "panoId": str(payload.get("pano_id"))[:80],
        "lat": round(plat, 6), "lon": round(plon, 6),
        "distanceM": round(haversine_m((lat, lon), (plat, plon)), 1),
    }


def _mapillary(lat: float, lon: float, radius_m: float) -> dict | None:
    token = _mapillary_token()
    if not token:
        return None
    west, south, east, north = _bbox(lat, lon, radius_m)
    query = urllib.parse.urlencode({
        "fields": "id,computed_geometry,compass_angle",
        "bbox": f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}",
        "limit": 8,
    })
    status, body = _get(MAPILLARY_IMAGES + "?" + query, headers={"Authorization": f"OAuth {token}"})
    if status != 200 or not body:
        return None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    best = None
    best_d = None
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        geom = row.get("computed_geometry") if isinstance(row.get("computed_geometry"), dict) else {}
        coords = geom.get("coordinates") if isinstance(geom.get("coordinates"), list) else None
        if not coords or len(coords) < 2:
            continue
        try:
            plon, plat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        dist = haversine_m((lat, lon), (plat, plon))
        if dist > radius_m + 5:
            continue
        if best_d is None or dist < best_d:
            best_d = dist
            best = {
                "ok": True, "provider": "mapillary", "panoId": str(row.get("id"))[:80],
                "lat": round(plat, 6), "lon": round(plon, 6), "distanceM": round(dist, 1),
            }
    return best


def nearest(lat: float, lon: float, radius_m: float = LOOKUP_RADIUS_M, prefer: str | None = None) -> dict:
    """Nearest panorama. ``prefer`` is ``google`` or ``mapillary``; omit to try Google, then Mapillary."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_points", "message": "A panorama lookup needs lat and lon."}
    radius = max(10.0, min(80.0, float(radius_m or LOOKUP_RADIUS_M)))
    order = ["google", "mapillary"]
    choice = str(prefer or "").strip().lower()
    if choice in ("google", "streetview", "street-view"):
        order = ["google"]
    elif choice == "mapillary":
        order = ["mapillary"]
    found = None
    tried = []
    for name in order:
        tried.append(name)
        hit = _google(lat, lon, radius) if name == "google" else _mapillary(lat, lon, radius)
        if hit:
            found = hit
            break
    if not found:
        missing = []
        if "google" in order and not _google_key():
            missing.append("GOOGLE_MAPS_API_KEY")
        if "mapillary" in order and not _mapillary_token():
            missing.append("MAPILLARY_CLIENT_TOKEN")
        message = "No Street View or Mapillary panorama within the search radius."
        code = "no_pano"
        fix = (
            "Turn on the Street Level layer in the pane, or set GOOGLE_MAPS_API_KEY / "
            "MAPILLARY_CLIENT_TOKEN through Hermes. Do not print the key."
        )
        if missing and len(missing) == len(order):
            message = (
                "No street-level key is set in Hermes, so the server could not look up a "
                "panorama. The pane can still open one if its Street Level layer has a key."
            )
            code = "KEY_MISSING"
            fix = _HERMES_KEY_FIX
        return {
            "ok": False, "error": code, "message": _clean(message),
            "tried": tried, "missingKeys": missing,
            "fix": fix,
        }
    if found["provider"] != "google" and "google" in tried:
        found = {**found, "fallback": found["provider"]}
    return found
