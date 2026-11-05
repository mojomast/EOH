"""Keyless place search: Overpass (with public fallbacks + circuit breaker) and Nominatim.

Used by godseye_find_places / godseye_poi_details and to remediate pane osm_query /
resolve_area errors. Network goes through ``_http`` so tests can mock it."""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request

USER_AGENT = os.environ.get(
    "GODSEYE_HTTP_USER_AGENT",
    "hermes-godseye/0.2.0 (+https://github.com/mojomast/hermes-godseye; Hermes God's Eye View plugin)")
PUBLIC_OVERPASS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
NOMINATIM = os.environ.get("GODSEYE_NOMINATIM_URL", "https://nominatim.openstreetmap.org")
TIMEOUT_S = 20.0
NOMINATIM_GAP_S = 1.2
BREAKER_FAILS = 2
BREAKER_COOLDOWN_S = 300.0
MAX_RADIUS_M = 15000
TOTAL_BUDGET_S = 45.0

_lock = threading.Lock()
_breaker: dict[str, tuple[int, float]] = {}  # url -> (consecutive fails, open-until)
_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 3600.0
_last_nominatim = [0.0]


def _http(url: str, data: bytes | None = None, timeout: float = TIMEOUT_S) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json",
        **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(4_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def configured_overpass() -> list[str]:
    raw = os.environ.get("GODSEYE_OVERPASS_URL") or os.environ.get("OVERPASS_UPSTREAMS") or ""
    return [u.strip() for u in raw.split(",") if u.strip().startswith(("http://", "https://"))]


def overpass_chain() -> list[str]:
    out = []
    for u in configured_overpass() + PUBLIC_OVERPASS:
        if u not in out:
            out.append(u)
    return out


def recommended_upstreams_env() -> str:
    """Value for OVERPASS_UPSTREAMS in ~/.config/gods-eye-view.env so the pane's osm_query uses the same chain."""
    return "OVERPASS_UPSTREAMS=" + ",".join(overpass_chain())


def _breaker_open(url: str) -> bool:
    fails, until = _breaker.get(url, (0, 0.0))
    return until > time.monotonic()


def _record(url: str, ok: bool) -> None:
    with _lock:
        if ok:
            _breaker.pop(url, None)
            return
        fails = _breaker.get(url, (0, 0.0))[0] + 1
        _breaker[url] = (fails, time.monotonic() + BREAKER_COOLDOWN_S if fails >= BREAKER_FAILS else 0.0)


def overpass(query: str) -> dict:
    key = "op:" + query
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]  # type: ignore[return-value]
    errors = []
    t0 = time.monotonic()
    for url in overpass_chain():
        if time.monotonic() - t0 > TOTAL_BUDGET_S:
            errors.append("total budget spent")
            break
        if _breaker_open(url):
            errors.append(f"{urllib.parse.urlparse(url).hostname}: circuit open")
            continue
        try:
            status, body = _http(url, urllib.parse.urlencode({"data": query}).encode())
            data = json.loads(body) if status == 200 and body else None
            remark = str((data or {}).get("remark", "")).lower()
            if data is None or "runtime error" in remark or "rate_limited" in remark:
                raise RuntimeError(f"HTTP {status} {remark[:80]}".strip())
        except Exception as exc:  # timeout, refused, bad JSON
            _record(url, False)
            errors.append(f"{urllib.parse.urlparse(url).hostname}: {str(exc)[:120]}")
            continue
        _record(url, True)
        data["_endpoint"] = urllib.parse.urlparse(url).hostname
        _cache[key] = (time.time(), data)
        return data
    raise RuntimeError("All Overpass endpoints failed: " + "; ".join(errors))


def nominatim(path: str, params: dict) -> list | dict:
    params = {**params, "format": "jsonv2"}
    url = f"{NOMINATIM}/{path}?{urllib.parse.urlencode(params)}"
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]  # type: ignore[return-value]
    with _lock:
        wait = _last_nominatim[0] + NOMINATIM_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_nominatim[0] = time.monotonic()
    status, body = _http(url)
    if status != 200:
        raise RuntimeError(f"Nominatim HTTP {status}")
    data = json.loads(body or "[]")
    _cache[url] = (time.time(), data)
    return data


# --- categories -----------------------------------------------------------
CATEGORIES = {
    "restaurant": '["amenity"="restaurant"]', "cafe": '["amenity"="cafe"]', "bar": '["amenity"="bar"]',
    "pub": '["amenity"="pub"]', "fast_food": '["amenity"="fast_food"]', "hotel": '["tourism"="hotel"]',
    "museum": '["tourism"="museum"]', "hospital": '["amenity"="hospital"]', "pharmacy": '["amenity"="pharmacy"]',
    "bakery": '["shop"="bakery"]', "supermarket": '["shop"="supermarket"]', "park": '["leisure"="park"]',
    "school": '["amenity"="school"]', "library": '["amenity"="library"]', "fuel": '["amenity"="fuel"]',
    "parking": '["amenity"="parking"]', "atm": '["amenity"="atm"]', "bank": '["amenity"="bank"]',
    "cinema": '["amenity"="cinema"]', "attraction": '["tourism"="attraction"]',
}
CUISINES = {"ramen", "sushi", "pizza", "burger", "japanese", "chinese", "korean", "thai", "vietnamese", "indian",
            "italian", "mexican", "french", "greek", "lebanese", "noodle", "pho", "bagel", "poutine", "coffee_shop",
            "seafood", "steak_house", "vegan", "vegetarian", "dim_sum", "udon", "tacos", "bbq"}
ALIASES = {"restaurants": "restaurant", "cafes": "cafe", "coffee": "cafe", "bars": "bar", "pubs": "pub",
           "hotels": "hotel", "museums": "museum", "hospitals": "hospital", "pharmacies": "pharmacy",
           "bakeries": "bakery", "parks": "park", "schools": "school", "libraries": "library"}
_SPLIT = re.compile(r"\s+(?:in|near|around|at)\s+", re.I)


def parse_query(query: str) -> tuple[str | None, str | None, str | None, str | None]:
    """'ramen restaurants in Montreal' -> (category, cuisine, name_regex, place)."""
    q = (query or "").strip()
    place = None
    parts = _SPLIT.split(q, maxsplit=1)
    if len(parts) == 2:
        q, place = parts[0].strip(), parts[1].strip()
    cat = cuisine = None
    rest = []
    for w in re.findall(r"[\w'-]+", q.lower()):
        w2 = ALIASES.get(w, w)
        if w2 in CATEGORIES and not cat:
            cat = w2
        elif w2.rstrip("s") in CUISINES and not cuisine:
            cuisine = w2.rstrip("s")
        elif w2 in CUISINES and not cuisine:
            cuisine = w2
        else:
            rest.append(w)
    if cuisine and not cat:
        cat = "restaurant"
    name = " ".join(rest) or None
    return cat, cuisine, name, place


def bbox_around(lat: float, lon: float, radius_m: float) -> list[float]:
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.01, math.cos(math.radians(lat))))
    return [lat - dlat, lon - dlon, lat + dlat, lon + dlon]  # S, W, N, E


def bbox_polygon(bbox: list[float]) -> list[list[float]]:
    s, w, n, e = bbox
    return [[w, s], [e, s], [e, n], [w, n], [w, s]]


def geocode(place: str) -> dict | None:
    res = nominatim("search", {"q": place, "limit": 1, "addressdetails": 1})
    if not res:
        return None
    r = res[0]
    bb = [float(x) for x in r.get("boundingbox", [])] if r.get("boundingbox") else None  # S,N,W,E
    out = {"name": r.get("display_name"), "lat": float(r["lat"]), "lon": float(r["lon"]),
           "type": r.get("type"), "class": r.get("category") or r.get("class")}
    if bb and bb[0] != bb[1]:
        out["bbox"] = [bb[0], bb[2], bb[1], bb[3]]
    return out


def _addr(tags: dict) -> str | None:
    parts = [" ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x),
             tags.get("addr:city"), tags.get("addr:postcode")]
    s = ", ".join(p for p in parts if p)
    return s or None


def _poi(el: dict) -> dict | None:
    t = el.get("tags") or {}
    lat = el.get("lat", (el.get("center") or {}).get("lat"))
    lon = el.get("lon", (el.get("center") or {}).get("lon"))
    if lat is None or lon is None or not t.get("name"):
        return None
    return {"name": t["name"], "lat": round(float(lat), 6), "lon": round(float(lon), 6),
            "address": _addr(t), "website": t.get("website") or t.get("contact:website"),
            "opening_hours": t.get("opening_hours"), "cuisine": t.get("cuisine"),
            "phone": t.get("phone") or t.get("contact:phone"),
            "menu": t.get("website:menu") or t.get("menu:url"),
            **_extra_tags(t), **_facet_tags(t),
            "osm": f"{el.get('type')}/{el.get('id')}"}


def _extra_tags(t: dict) -> dict:
    """wikipedia / wikidata / image tags kept for the pin drawer (only when present)."""
    out = {k: t.get(k) for k in ("wikipedia", "wikidata", "image", "wikimedia_commons") if t.get(k)}
    return out


FACET_KEYS = ("amenity", "shop", "tourism", "leisure", "stars", "rating")


def _facet_tags(t: dict) -> dict:
    """Category / rating tags the pane's pin filter chips use (only when present)."""
    return {k: str(t[k])[:80] for k in FACET_KEYS if t.get(k)}


def pin_tags(p: dict) -> dict:
    """annotate_map item `tags` (facets for the pane filters) from a find_places row."""
    out = {k: p[k] for k in ("cuisine", "opening_hours", *FACET_KEYS) if p.get(k)}
    if p.get("category") and not any(out.get(k) for k in ("amenity", "shop", "tourism", "leisure")):
        out["category"] = p["category"]
    return {k: str(v)[:400] for k, v in out.items()}


def _esc(s: str) -> str:
    return re.sub(r"[\\\"]", "", s)


def find_places(args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    cat, cuisine, name, place = parse_query(query)
    if args.get("category"):
        cat = ALIASES.get(str(args["category"]).lower(), str(args["category"]).lower())
    if args.get("cuisine"):
        cuisine = str(args["cuisine"]).lower()
    near = args.get("near") or place
    limit = max(1, min(int(args.get("limit") or 25), 100))
    radius = float(args.get("radiusM") or 0)
    center = None
    bbox = args.get("bbox")
    bbox_warning = None
    if bbox:
        from .areas import interpret_bbox
        try:
            parsed = interpret_bbox(bbox, prefer="swne")
        except ValueError as exc:
            return {"ok": False, "code": "BAD_AREA", "error": str(exc)}
        bbox = parsed["bbox"]
        bbox_warning = parsed.get("warning")
    elif isinstance(near, dict) or (args.get("lat") is not None and args.get("lon") is not None):
        src = near if isinstance(near, dict) else args
        center = {"lat": float(src["lat"]), "lon": float(src["lon"]), "name": src.get("name")}
    elif near:
        g = geocode(str(near))
        if not g:
            return {"ok": False, "code": "PLACE_NOT_FOUND", "error": f"Nominatim found no place named {near!r}."}
        center = g
        if not radius and g.get("bbox"):
            bbox = g["bbox"]
    if not bbox:
        if not center:
            return {"ok": False, "code": "NO_AREA", "error": "Give near (place or {lat,lon}), lat/lon, or bbox."}
        bbox = bbox_around(center["lat"], center["lon"], min(radius or 3000, MAX_RADIUS_M))
    # clamp huge admin bboxes (Overpass load) to MAX_RADIUS_M around the centre
    s, w, n, e = bbox
    if (n - s) * 111 > 2 * MAX_RADIUS_M / 1000 * 1.5 and center:
        bbox = bbox_around(center["lat"], center["lon"], MAX_RADIUS_M)
        s, w, n, e = bbox
    if not cat and not cuisine and name:
        # name / address lookup via Nominatim inside the box
        res = nominatim("search", {"q": name, "limit": limit, "viewbox": f"{w},{n},{e},{s}", "bounded": 1,
                                   "addressdetails": 1, "extratags": 1})
        pois = [{"name": r.get("name") or r.get("display_name"), "lat": float(r["lat"]), "lon": float(r["lon"]),
                 "address": r.get("display_name"), "website": (r.get("extratags") or {}).get("website"),
                 "opening_hours": (r.get("extratags") or {}).get("opening_hours"),
                 **_extra_tags(r.get("extratags") or {}), "osm": f"{r.get('osm_type')}/{r.get('osm_id')}"}
                for r in res]
        return _finish(args, pois, "nominatim", bbox, query, center)
    filt = CATEGORIES.get(cat or "", f'["amenity"="{_esc(cat)}"]' if cat else "")
    if cuisine:
        c = _esc(cuisine)
        # ramen places are often tagged cuisine=ramen/japanese with "ramen" in the name
        filters = [f'{filt}["cuisine"~"{c}",i]', f'{filt}["name"~"{c}",i]']
    else:
        filters = [filt + (f'["name"~"{_esc(name)}",i]' if name else "")]
    box = f"({s},{w},{n},{e})"
    body = "".join(f"nwr{f}{box};" for f in filters)
    q = f"[out:json][timeout:18];({body});out center tags {limit * 3};"
    try:
        data = overpass(q)
    except RuntimeError as exc:
        pois = _nominatim_category(cat, cuisine, name, bbox, limit)
        if pois and center:
            pois.sort(key=lambda p: (p["lat"] - center["lat"]) ** 2 + (p["lon"] - center["lon"]) ** 2)
        if pois:
            out = _finish(args, pois, "nominatim (Overpass unavailable)", bbox, query, center)
            out["overpassError"] = str(exc)[:400]
            out["countScope"] = "Nominatim keyword matches in the queried extent (may miss untagged-name places)"
            return out
        return {"ok": False, "code": "OSM_UNAVAILABLE", "error": str(exc)}
    seen, pois = set(), []
    for el in data.get("elements", []):
        p = _poi(el)
        if p and p["osm"] not in seen:
            seen.add(p["osm"])
            pois.append(p)
    if center:
        pois.sort(key=lambda p: (p["lat"] - center["lat"]) ** 2 + (p["lon"] - center["lon"]) ** 2)
    finished = _finish(args, pois[:limit], f"overpass:{data.get('_endpoint')}", bbox, query, center)
    if bbox_warning and isinstance(finished, dict):
        finished["bboxWarning"] = bbox_warning
    return finished


def _nominatim_category(cat, cuisine, name, bbox, limit) -> list[dict]:
    """Fallback when every Overpass mirror is down: Nominatim keyword search bounded to the box."""
    s, w, n, e = bbox
    word = cuisine or name or cat
    if not word:
        return []
    want_type = (CATEGORIES.get(cat or "") or "").split('"')[3] if cat in CATEGORIES else None
    pois, exclude = [], []
    for _ in range(3):  # Nominatim caps limit at 40; page with exclude_place_ids
        params = {"q": word.replace("_", " "), "limit": 40, "viewbox": f"{w},{n},{e},{s}", "bounded": 1,
                  "addressdetails": 1, "extratags": 1}
        if exclude:
            params["exclude_place_ids"] = ",".join(exclude)
        try:
            res = nominatim("search", params)
        except Exception:
            break
        if not res:
            break
        for r in res:
            exclude.append(str(r.get("place_id")))
            if want_type and r.get("type") not in (want_type, "fast_food", "cafe", "bar", "pub"):
                continue
            ex, ad = r.get("extratags") or {}, r.get("address") or {}
            addr = ", ".join(x for x in (" ".join(y for y in (ad.get("house_number"), ad.get("road")) if y),
                                         ad.get("city") or ad.get("town"), ad.get("postcode")) if x) or r.get("display_name")
            pois.append({"name": r.get("name") or r.get("display_name"), "lat": round(float(r["lat"]), 6),
                         "lon": round(float(r["lon"]), 6), "address": addr,
                         "website": ex.get("website") or ex.get("contact:website"),
                         "opening_hours": ex.get("opening_hours"), "cuisine": ex.get("cuisine"),
                         "phone": ex.get("phone"), "menu": ex.get("website:menu"), **_extra_tags(ex),
                         "osm": f"{r.get('osm_type')}/{r.get('osm_id')}"})
        if len(res) < 40 or len(pois) >= limit:
            break
    return pois[:limit]


def _finish(args, pois, source, bbox, query, center) -> dict:
    return {"ok": True, "query": query, "count": len(pois), "source": source,
            "countScope": "queried extent (bbox), OSM-tagged places only", "bbox": [round(x, 5) for x in bbox],
            "center": center, "places": pois,
            "attribution": "© OpenStreetMap contributors (ODbL)"}


def _http_url(u) -> str | None:
    u = str(u or "").strip()
    if u and not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I) and re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", u):
        u = "https://" + u  # OSM often tags bare domains
    return u if re.match(r"^https?://[^\s<>\"']+$", u, re.I) else None


def _md(s) -> str:
    """Escape markdown control characters in OSM tag text."""
    return re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", str(s))


def commons_image(tag) -> str | None:
    """Wikimedia Commons file -> a resolvable Special:FilePath URL (no request is made here)."""
    tag = str(tag or "").strip()
    m = re.match(r"^(?:File|Image):(.+)$", tag, re.I)
    if not m:
        return None
    from urllib.parse import quote
    return "https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(m.group(1).strip().replace(" ", "_")) + "?width=640"


def drawer_fields(p: dict) -> dict:
    """Pin-drawer fields (description_md, images[], links[]) from the OSM tags already fetched."""
    lines = []
    if p.get("cuisine"):
        lines.append(f"**Cuisine:** {_md(str(p['cuisine']).replace(';', ', '))}")
    if p.get("address"):
        lines.append(f"**Address:** {_md(p['address'])}")
    if p.get("opening_hours"):
        lines.append(f"**Hours:** `{str(p['opening_hours']).replace('`', '')}`")
    if p.get("phone"):
        lines.append(f"**Phone:** {_md(p['phone'])}")
    links = []
    for label, key in (("Website", "website"), ("Menu", "menu")):
        u = _http_url(p.get(key))
        if u:
            links.append({"label": label, "url": u})
    wp = str(p.get("wikipedia") or "")
    if ":" in wp:
        from urllib.parse import quote
        lang, title = wp.split(":", 1)
        if re.match(r"^[a-z-]{2,12}$", lang):
            links.append({"label": "Wikipedia", "url": f"https://{lang}.wikipedia.org/wiki/{quote(title.strip().replace(' ', '_'))}"})
    if re.match(r"^Q\d+$", str(p.get("wikidata") or "")):
        links.append({"label": "Wikidata", "url": f"https://www.wikidata.org/wiki/{p['wikidata']}"})
    if re.match(r"^(node|way|relation|[NWR])/\d+$", str(p.get("osm") or "")):
        kind, oid = p["osm"].split("/")
        kind = {"N": "node", "W": "way", "R": "relation"}.get(kind, kind)
        links.append({"label": "OpenStreetMap", "url": f"https://www.openstreetmap.org/{kind}/{oid}"})
    images = []
    for u in (commons_image(p.get("wikimedia_commons")), commons_image(p.get("image")), _http_url(p.get("image"))):
        if u and not any(i["url"] == u for i in images):
            images.append({"url": u, "alt": str(p.get("name") or "photo")[:120]})
    out = {"title": str(p.get("name") or "")[:120], "description_md": "\n\n".join(lines) or None,
           "links": links, "images": images[:2]}
    return {k: v for k, v in out.items() if v}


def pin_annotations(pois: list[dict], label_prefix: str = "", intent: str | None = None) -> list[dict]:
    """Build annotate_map pins with drawer content shaped by the user's intent."""
    from . import enrich
    out = []
    for p in pois[:24]:
        base = {"type": "pin", "target": p["name"][:200], "latitude": p["lat"], "longitude": p["lon"],
                "label": f"{label_prefix}{p['name']}"[:80], **drawer_fields(p),
                **({"tags": pin_tags(p)} if pin_tags(p) else {})}
        out.append(enrich.ensure_drawer(base, intent=intent or p.get("name"), mode="light", extra=p))
    return out


# --- P2: details ------------------------------------------------------------
_PRICE = re.compile(r"(?:\$|CA\$|C\$|€|£)\s?\d{1,3}(?:[.,]\d{2})?|\d{1,3}[.,]\d{2}\s?\$")
_MENU_LINK = re.compile(r"""href=["']([^"']*(?:menu|carte)[^"']*)["']""", re.I)
_TAGS = re.compile(r"<[^>]+>")


def poi_details(args: dict) -> dict:
    name, city = str(args.get("name") or "").strip(), str(args.get("city") or "").strip()
    if not name:
        return {"ok": False, "code": "BAD_ARGS", "error": "name is required"}
    place = None
    g = nominatim("search", {"q": f"{name}, {city}".strip(", "), "limit": 1, "extratags": 1})
    if g:
        ex = g[0].get("extratags") or {}
        place = {"name": g[0].get("name") or name, "lat": float(g[0]["lat"]), "lon": float(g[0]["lon"]),
                 "address": g[0].get("display_name"), "website": ex.get("website") or ex.get("contact:website"),
                 "opening_hours": ex.get("opening_hours"), "menu": ex.get("website:menu")}
    if not place:
        return {"ok": False, "code": "NOT_FOUND", "error": f"No OSM record for {name!r} {city!r}."}
    out = {"ok": True, **place, "menu_url": place.get("menu"), "prices": [], "notes": []}
    site = place.get("website")
    if site:
        try:
            status, html = _http(site, timeout=12)
            if status == 200 and html:
                if not out["menu_url"]:
                    m = _MENU_LINK.search(html)
                    if m:
                        out["menu_url"] = urllib.parse.urljoin(site, m.group(1))
                page = html
                if out["menu_url"] and out["menu_url"] != site:
                    s2, h2 = _http(out["menu_url"], timeout=12)
                    if s2 == 200 and h2:
                        page = h2
                text = re.sub(r"\s+", " ", _TAGS.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", page)))
                rows = []
                for m in _PRICE.finditer(text):
                    snippet = text[max(0, m.start() - 60):m.end()].strip()
                    rows.append(snippet[-100:])
                    if len(rows) >= 20:
                        break
                out["prices"] = rows
                if not rows:
                    out["notes"].append("No price text found on the site (may be an image/PDF/JS menu).")
            else:
                out["notes"].append(f"Website returned HTTP {status}.")
        except Exception as exc:
            out["notes"].append(f"Website fetch failed: {str(exc)[:120]}")
    else:
        out["notes"].append("No website tagged in OSM.")
    out["source"] = "OpenStreetMap + the venue's own website (verbatim snippets, best effort)"
    return out


# --- remediation for pane errors -------------------------------------------
def remediate(name: str, out: dict) -> dict:
    blob = json.dumps(out)
    if "AREA_TOO_LARGE" in blob:
        out["remediation"] = ("Area too large for the pane's OSM search: fly with rangeM <= 15000 first "
                              "(godseye_view op=fly), or use godseye_query op=find_places which clamps the box.")
    if "OVERPASS_NOT_CONFIGURED" in blob:
        out["remediation"] = (
            "The app has no Overpass server configured, so in-pane osm is disabled. Use "
            "godseye_query op=find_places now (it uses public keyless Overpass mirrors). To enable osm add "
            f"`{recommended_upstreams_env()}` to ~/.config/gods-eye-view.env and restart gods-eye-view.service.")
    return out


def resolve_area_fallback(args: dict, out: dict, call) -> dict:
    """Point-only places: retry the pane's resolve_area with around:true (a buffer polygon)."""
    if not isinstance(out, dict) or args.get("around") or not args.get("query"):
        return out
    blob = json.dumps(out)
    if not any(c in blob for c in ("AREA_NOT_FOUND", "AREA_UNAVAILABLE", "AREA_IDENTITY")):
        return out
    retry = call({**args, "around": True, "radiusM": args.get("radiusM") or 1000})
    if isinstance(retry, dict) and '"ok": true' in json.dumps(retry).lower().replace("'", '"'):
        retry["degraded"] = "point place: outlined as a buffer around it (around:true)"
        return retry
    g = None
    try:
        g = geocode(str(args["query"]))
    except Exception:
        pass
    if g:
        bb = g.get("bbox") or bbox_around(g["lat"], g["lon"], float(args.get("radiusM") or 1000))
        out["fallback"] = {"bbox": bb, "polygon": bbox_polygon(bb), "center": [g["lat"], g["lon"]],
                           "note": "Nominatim point; bbox polygon. Use with godseye_query op=find_places bbox=..."}
    return out
