"""Measure, reach and "what is here" for Eye Of Hermes (EOH).

Pure geometry (great-circle distance, path length, spherical polygon area) plus three keyless
public services, each degrading to a clear ``{ok: false, error, message}`` when unreachable:

- reverse geocoding: Nominatim ``/reverse`` (shared rate limit + User-Agent with gev/poi.py, 1.2 s gap)
- isochrones: Valhalla public (valhalla1.openstreetmap.de), else an approximate reach polygon built
  from one OSRM ``table`` call (routing.openstreetmap.de, FOSSGIS) on 16 bearings x 4 radii
- routes: OSRM ``route`` (routing.openstreetmap.de, profiles car / foot / bike)
- historical imagery: Esri World Imagery Wayback release list (keyless WMTS)
"""
from __future__ import annotations

import json
import math
import os
import urllib.parse

from . import poi

R_EARTH = 6371008.8  # mean Earth radius (m)
VALHALLA = os.environ.get("GODSEYE_VALHALLA_URL", "https://valhalla1.openstreetmap.de")
OSRM = os.environ.get("GODSEYE_OSRM_URL", "https://routing.openstreetmap.de")
WAYBACK_CONFIG = os.environ.get("GODSEYE_WAYBACK_CONFIG",
                                "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json")
WAYBACK_TILES = "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{release}/{z}/{y}/{x}"
MODES = {"walk": ("pedestrian", "routed-foot", "foot", 5.0), "bike": ("bicycle", "routed-bike", "bike", 16.0),
         "drive": ("auto", "routed-car", "driving", 50.0)}  # valhalla costing, OSRM host path, OSRM profile, km/h guess
MODE_ALIASES = {"walking": "walk", "foot": "walk", "pedestrian": "walk", "driving": "drive", "car": "drive",
                "auto": "drive", "cycling": "bike", "bicycle": "bike", "cycle": "bike"}
_http = poi._http  # shared User-Agent + timeout (tests patch measure._http)


# ---- geometry --------------------------------------------------------------------------------
def _pt(p) -> tuple[float, float]:
    """(lat, lon) from {lat, lon} / {latitude, longitude} / [lat, lon]."""
    if isinstance(p, dict):
        lat = p.get("lat", p.get("latitude"))
        lon = p.get("lon", p.get("lng", p.get("longitude")))
    else:
        lat, lon = p[0], p[1]
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"point out of range: {lat}, {lon}")
    return lat, lon


def haversine_m(a, b) -> float:
    (la1, lo1), (la2, lo2) = _pt(a), _pt(b)
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a, b) -> float:
    (la1, lo1), (la2, lo2) = _pt(a), _pt(b)
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination(p, bearing: float, dist_m: float) -> tuple[float, float]:
    lat, lon = map(math.radians, _pt(p))
    br, d = math.radians(bearing), dist_m / R_EARTH
    lat2 = math.asin(math.sin(lat) * math.cos(d) + math.cos(lat) * math.sin(d) * math.cos(br))
    lon2 = lon + math.atan2(math.sin(br) * math.sin(d) * math.cos(lat), math.cos(d) - math.sin(lat) * math.sin(lat2))
    return round(math.degrees(lat2), 6), round((math.degrees(lon2) + 540) % 360 - 180, 6)


def path_length_m(points) -> float:
    pts = [_pt(p) for p in points]
    return sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def polygon_area_m2(points) -> float:
    """Area of a simple polygon on the sphere (ring may be open or closed)."""
    pts = [_pt(p) for p in points]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("a polygon needs at least 3 points")
    total = 0.0
    for i in range(len(pts)):
        (la1, lo1), (la2, lo2) = pts[i], pts[(i + 1) % len(pts)]
        dl = math.radians(lo2 - lo1)
        dl = (dl + math.pi) % (2 * math.pi) - math.pi  # across the antimeridian
        total += dl * (2 + math.sin(math.radians(la1)) + math.sin(math.radians(la2)))
    return abs(total * R_EARTH ** 2 / 2)


def centroid(points) -> tuple[float, float]:
    pts = [_pt(p) for p in points]
    x = y = z = 0.0
    for la, lo in pts:
        la, lo = math.radians(la), math.radians(lo)
        x += math.cos(la) * math.cos(lo); y += math.cos(la) * math.sin(lo); z += math.sin(la)
    return round(math.degrees(math.atan2(z, math.hypot(x, y))), 6), round(math.degrees(math.atan2(y, x)), 6)


def fmt_distance(m: float) -> str:
    return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.2f} km" if m < 100000 else f"{m / 1000:.0f} km"


def fmt_area(m2: float) -> str:
    if m2 < 10000:
        return f"{m2:.0f} m²"
    if m2 < 1e6:
        return f"{m2 / 10000:.2f} ha"
    return f"{m2 / 1e6:.2f} km²"


def mode_of(mode: str | None) -> str:
    m = str(mode or "walk").lower()
    m = MODE_ALIASES.get(m, m)
    if m not in MODES:
        raise ValueError(f"mode must be walk, bike or drive (got {mode!r})")
    return m


# ---- measure ---------------------------------------------------------------------------------
def measure(args: dict) -> dict:
    """op=distance (2+ points: great-circle total + per leg), op=area (3+ points), op=route (OSRM)."""
    op = str(args.get("op") or ("area" if args.get("polygon") else "distance")).lower()
    pts = args.get("points") or args.get("polygon") or []
    try:
        pts = [_pt(p) for p in pts]
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        return {"ok": False, "error": "bad_points", "message": f"points must be [{{lat, lon}}, ...]: {exc}"}
    if op == "area":
        if len(pts) < 3:
            return {"ok": False, "error": "bad_points", "message": "area needs 3+ points (or a drawn polygon/box)."}
        a = polygon_area_m2(pts)
        per = path_length_m(pts + [pts[0]])
        return {"ok": True, "op": "area", "areaM2": round(a, 1), "areaKm2": round(a / 1e6, 6), "label": fmt_area(a),
                "perimeterM": round(per, 1), "centroid": dict(zip(("lat", "lon"), centroid(pts))), "points": len(pts),
                "method": "spherical polygon (mean Earth radius); ignores terrain relief"}
    if len(pts) < 2:
        return {"ok": False, "error": "bad_points", "message": "distance needs 2+ points (pins, coordinates or a drawn line)."}
    if op == "route":
        return route(pts, args.get("mode"))
    legs = [{"from": i, "to": i + 1, "m": round(haversine_m(pts[i], pts[i + 1]), 1),
             "bearingDeg": round(bearing_deg(pts[i], pts[i + 1]), 1)} for i in range(len(pts) - 1)]
    total = sum(l["m"] for l in legs)
    return {"ok": True, "op": "distance", "meters": round(total, 1), "km": round(total / 1000, 4),
            "miles": round(total / 1609.344, 4), "label": fmt_distance(total), "legs": legs,
            "method": "great-circle (haversine, mean Earth radius)"}


def route(pts, mode=None) -> dict:
    try:
        m = mode_of(mode)
    except ValueError as exc:
        return {"ok": False, "error": "bad_mode", "message": str(exc)}
    _c, host, prof, _k = MODES[m]
    coords = ";".join(f"{lo},{la}" for la, lo in pts)
    url = f"{OSRM}/{host}/route/v1/{prof}/{coords}?overview=simplified&geometries=geojson"
    try:
        status, body = _http(url, timeout=20)
        data = json.loads(body or "{}") if status == 200 else {}
    except Exception as exc:  # network down, DNS, TLS
        return _routing_down("OSRM", exc)
    if status != 200 or data.get("code") != "Ok" or not data.get("routes"):
        return _routing_down("OSRM", f"HTTP {status} {data.get('code') or ''}".strip())
    r = data["routes"][0]
    line = [[round(la, 6), round(lo, 6)] for lo, la in (r.get("geometry") or {}).get("coordinates", [])]
    return {"ok": True, "op": "route", "mode": m, "meters": round(r["distance"], 1), "label": fmt_distance(r["distance"]),
            "minutes": round(r["duration"] / 60, 1), "line": line[:500],
            "greatCircleM": round(path_length_m(pts), 1), "source": f"OSRM ({OSRM}/{host})"}


def _routing_down(name, why) -> dict:
    return {"ok": False, "error": "routing_unavailable",
            "message": f"The public routing service ({name}) is unreachable or refused the request ({str(why)[:160]}). "
                       "Great-circle distance still works (op=distance); try again later or set GODSEYE_OSRM_URL / "
                       "GODSEYE_VALHALLA_URL to a reachable server."}


# ---- isochrones ------------------------------------------------------------------------------
def isochrone(args: dict) -> dict:
    """Reach within N minutes by walk/bike/drive: Valhalla public, else an OSRM-table approximation."""
    try:
        lat, lon = _pt({"lat": args.get("lat"), "lon": args.get("lon")})
        m = mode_of(args.get("mode"))
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": "bad_args", "message": str(exc)}
    mins = args.get("minutes") or [10]
    mins = sorted({max(1, min(120, int(x))) for x in (mins if isinstance(mins, list) else [mins])})[:4]
    errors = []
    out = _valhalla(lat, lon, m, mins, errors)
    if out is None:
        out = _osrm_reach(lat, lon, m, mins, errors)
    if out is None:
        return {"ok": False, "error": "routing_unavailable",
                "message": "No public routing service answered (" + "; ".join(errors)[:300] + "). The reach area cannot be "
                           "computed right now; a straight-line radius is NOT a substitute for travel time.",
                "center": {"lat": lat, "lon": lon}, "mode": m, "minutes": mins}
    for c in out["contours"]:
        if len(c["polygon"]) >= 3:
            c["areaKm2"] = round(polygon_area_m2(c["polygon"]) / 1e6, 3)
    return {"ok": True, "center": {"lat": lat, "lon": lon}, "mode": m, "minutes": mins, **out,
            **({"fallbackFrom": errors} if errors else {})}


def _valhalla(lat, lon, m, mins, errors) -> dict | None:
    q = {"locations": [{"lat": lat, "lon": lon}], "costing": MODES[m][0],
         "contours": [{"time": t} for t in mins], "polygons": True, "denoise": 0.5, "generalize": 40}
    url = f"{VALHALLA}/isochrone?json={urllib.parse.quote(json.dumps(q, separators=(',', ':')))}"
    try:
        status, body = _http(url, timeout=25)
        data = json.loads(body or "{}") if status == 200 else {}
    except Exception as exc:
        errors.append(f"Valhalla: {type(exc).__name__}")
        return None
    feats = data.get("features") or []
    if status != 200 or not feats:
        errors.append(f"Valhalla: HTTP {status}")
        return None
    contours = []
    for f in feats:
        geom = f.get("geometry") or {}
        ring = geom.get("coordinates") or []
        if geom.get("type") == "MultiPolygon":
            ring = max((p[0] for p in ring), key=len, default=[])
        elif geom.get("type") == "Polygon":
            ring = ring[0] if ring else []
        t = (f.get("properties") or {}).get("contour")
        contours.append({"minutes": int(t) if isinstance(t, float) and t.is_integer() else t,
                         "polygon": [[round(la, 6), round(lo, 6)] for lo, la in ring][:600]})
    contours.sort(key=lambda c: c["minutes"] or 0)
    return {"contours": contours, "source": f"Valhalla ({VALHALLA})", "approximate": False}


def _osrm_reach(lat, lon, m, mins, errors, bearings: int = 16) -> dict | None:
    _c, host, prof, kmh = MODES[m]
    rmax = kmh * 1000 / 60 * max(mins) * 1.15  # a bit past the free-flow distance
    fracs = (0.25, 0.5, 0.75, 1.0)
    pts = [(lat, lon)] + [destination((lat, lon), b * 360 / bearings, rmax * f) for b in range(bearings) for f in fracs]
    coords = ";".join(f"{lo},{la}" for la, lo in pts)
    url = f"{OSRM}/{host}/table/v1/{prof}/{coords}?sources=0&annotations=duration"
    try:
        status, body = _http(url, timeout=25)
        data = json.loads(body or "{}") if status == 200 else {}
    except Exception as exc:
        errors.append(f"OSRM: {type(exc).__name__}")
        return None
    row = (data.get("durations") or [None])[0]
    if status != 200 or data.get("code") != "Ok" or not row:
        errors.append(f"OSRM: HTTP {status}")
        return None
    contours = []
    for t in mins:
        ring = []
        for b in range(bearings):
            best = 0.0
            for j, f in enumerate(fracs):
                d = row[1 + b * len(fracs) + j]
                if d is not None and d <= t * 60:
                    best = f
            ring.append(list(destination((lat, lon), b * 360 / bearings, max(rmax * best, 30.0))))
        contours.append({"minutes": t, "polygon": ring})
    return {"contours": contours, "source": f"OSRM table ({OSRM}/{host})", "approximate": True,
            "note": f"Approximate: travel time sampled on {bearings} bearings x {len(fracs)} distances (Valhalla unavailable)."}


# ---- what is here ----------------------------------------------------------------------------
def reverse_geocode(lat: float, lon: float, zoom: int = 18) -> dict:
    """Nominatim /reverse (rate limited to one request per 1.2 s, descriptive User-Agent)."""
    try:
        r = poi.nominatim("reverse", {"lat": lat, "lon": lon, "zoom": zoom, "addressdetails": 1, "extratags": 1, "namedetails": 0})
    except Exception as exc:
        return {"ok": False, "error": "geocoder_unavailable", "message": f"Nominatim reverse geocoding failed: {exc}"}
    if not isinstance(r, dict) or r.get("error"):
        return {"ok": False, "error": "no_match", "message": str((r or {}).get("error") or "nothing at this point")}
    tags = r.get("extratags") or {}
    out = {"ok": True, "name": r.get("name") or None, "displayName": r.get("display_name"),
           "category": r.get("category"), "type": r.get("type"), "address": r.get("address") or {},
           "osm": f"{r.get('osm_type')}/{r.get('osm_id')}" if r.get("osm_id") else None,
           "lat": float(r.get("lat") or lat), "lon": float(r.get("lon") or lon),
           "distanceM": round(haversine_m((lat, lon), (float(r.get("lat") or lat), float(r.get("lon") or lon))), 1)}
    for k in ("website", "wikipedia", "wikidata", "opening_hours", "building:levels", "start_date", "height"):
        if tags.get(k):
            out.setdefault("tags", {})[k] = tags[k]
    return out


def what_is_here(lat: float, lon: float, radius_m: float = 150, nearby: bool = True, category: str | None = None,
                 limit: int = 8) -> dict:
    lat, lon = _pt((lat, lon))
    out = {"ok": True, "point": {"lat": lat, "lon": lon}, "place": reverse_geocode(lat, lon)}
    if nearby:
        out["nearby"] = nearby_named(lat, lon, radius_m, category, limit)
    return out


NEARBY_KEYS = ("amenity", "tourism", "shop", "historic", "leisure", "building", "office")


def nearby_named(lat: float, lon: float, radius_m: float = 150, category: str | None = None, limit: int = 8) -> dict:
    """Named POIs/buildings around a point, nearest first (Overpass via gev/poi.py; category uses find_places)."""
    r = max(30, min(2000, float(radius_m or 150)))
    try:
        if category:
            res = poi.find_places({"lat": lat, "lon": lon, "radiusM": r, "category": category, "limit": limit * 2})
            if not res.get("ok"):
                return {"ok": False, "error": res.get("code") or "poi_failed", "message": res.get("error")}
            places, source = res.get("places") or [], res.get("source")
        else:
            body = "".join(f'nwr["name"]["{k}"](around:{r:.0f},{lat},{lon});' for k in NEARBY_KEYS)
            data = poi.overpass(f"[out:json][timeout:15];({body});out center tags {limit * 4};")
            places, seen = [], set()
            for el in data.get("elements", []):
                p = poi._poi(el)
                if p and p["osm"] not in seen:
                    seen.add(p["osm"])
                    t = el.get("tags") or {}
                    p["kind"] = next((f"{k}={t[k]}" for k in NEARBY_KEYS if t.get(k)), None)
                    places.append(p)
            source = f"overpass:{data.get('_endpoint')}"
    except Exception as exc:
        if category:
            return {"ok": False, "error": "osm_unavailable", "message": f"Nearby lookup failed: {str(exc)[:200]}"}
        places = _reverse_ring(lat, lon, r)
        if not places:
            return {"ok": False, "error": "osm_unavailable", "message": f"Nearby lookup failed: {str(exc)[:200]}"}
        source = "nominatim reverse sampling (Overpass unavailable)"
    for p in places:
        if p.get("lat") is not None:
            p["distanceM"] = round(haversine_m((lat, lon), (p["lat"], p["lon"])), 1)
    places.sort(key=lambda p: p.get("distanceM", 1e9))
    return {"ok": True, "source": source, "radiusM": r, "places": places[:limit]}


def _reverse_ring(lat: float, lon: float, r: float) -> list[dict]:
    """Overpass fallback: named objects from Nominatim /reverse at the point and 4 points r/2 away
    (5 requests, rate limited to one per 1.2 s)."""
    out, seen = [], set()
    for b in (None, 0, 90, 180, 270):
        p = (lat, lon) if b is None else destination((lat, lon), b, r / 2)
        try:
            g = poi.nominatim("reverse", {"lat": p[0], "lon": p[1], "zoom": 18, "extratags": 1})
        except Exception:
            break
        if not isinstance(g, dict) or not g.get("name") or not g.get("osm_id"):
            continue
        osm = f"{g.get('osm_type')}/{g.get('osm_id')}"
        if osm in seen:
            continue
        seen.add(osm)
        ex = g.get("extratags") or {}
        out.append({"name": g["name"], "lat": round(float(g["lat"]), 6), "lon": round(float(g["lon"]), 6),
                    "address": g.get("display_name"), "website": ex.get("website"), "kind": f"{g.get('category')}={g.get('type')}",
                    "osm": osm})
    return out


# ---- historical imagery (Esri Wayback) -------------------------------------------------------
def wayback_releases() -> list[dict]:
    """[{release, date 'YYYY-MM-DD', title}] newest first; [] when the config is unreachable."""
    try:
        status, body = _http(WAYBACK_CONFIG, timeout=15)
        cfg = json.loads(body or "{}") if status == 200 else {}
    except Exception:
        return []
    out = []
    for rid, v in cfg.items():
        title = str((v or {}).get("itemTitle") or "")
        date = title.split("Wayback")[-1].strip(" ()") if "Wayback" in title else ""
        try:
            out.append({"release": int(rid), "date": date, "title": title})
        except ValueError:
            continue
    return sorted(out, key=lambda r: r["date"], reverse=True)


def pick_release(releases: list[dict], when: str) -> dict | None:
    """Closest release on or before ``when`` (YYYY, YYYY-MM or YYYY-MM-DD); else the oldest after it."""
    if not releases:
        return None
    w = str(when).strip()
    w = (w + "-12-31")[:10] if len(w) == 4 else (w + "-31")[:10] if len(w) == 7 else w[:10]
    before = [r for r in releases if r["date"] and r["date"] <= w]
    if before:
        return max(before, key=lambda r: r["date"])
    return min((r for r in releases if r["date"]), key=lambda r: r["date"], default=None)


def compare_layer(spec) -> dict:
    """Resolve one side of a compare: 'current' | 'wayback:YYYY[-MM[-DD]]' | 'osm' | a map stack id."""
    s = str(spec or "current").strip()
    low = s.lower()
    if low in ("current", "now", "live", ""):
        return {"kind": "current", "label": "Current view"}
    if low == "osm":
        return {"kind": "xyz", "label": "OpenStreetMap", "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "credit": "© OpenStreetMap contributors", "maxZoom": 19}
    if low.startswith("wayback") or low[:4].isdigit():
        when = s.split(":", 1)[1] if ":" in s else (s if s[:4].isdigit() else "")
        rels = wayback_releases()
        if not rels:
            return {"kind": "error", "error": "wayback_unavailable",
                    "message": "Esri World Imagery Wayback is unreachable; historical imagery cannot be shown right now."}
        r = pick_release(rels, when) if when else rels[-1]
        return {"kind": "xyz", "label": f"Esri Wayback {r['date']}", "release": r["release"], "date": r["date"],
                "url": WAYBACK_TILES.replace("{release}", str(r["release"])), "credit": "Esri World Imagery Wayback",
                "maxZoom": 19, "requested": when or "oldest"}
    return {"kind": "stack", "label": s, "stack": s}
