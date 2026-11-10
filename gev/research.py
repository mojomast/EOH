"""Research pins: fill each pin's drawer from free keyless sources (Wikipedia/Wikidata,
Wikimedia Commons, OSM tags already on the pin, website meta description).

Never invents facts. Missing data is marked unavailable. Results are cached under
plugin-data/godseye/research-cache.json. Cap work per pin and fail gracefully.

Hermes agent web search: this plugin does not call Hermes's own web_search / browser tools
(those live on the agent side). When Wikipedia/OSM are thin, the agent should:
  1. Run its own web_search (or browse) for the place,
  2. Pass verified snippets via godseye_research_pins {agent_notes:[{id|label, summary, links[], images[]}]}
     or rewrite the pin with annotate_map {description_md, images, links}.
Never invent from the agent's memory; only pass what the search actually returned.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = os.environ.get(
    "GODSEYE_HTTP_USER_AGENT",
    "hermes-godseye/0.2.9 (+https://github.com/mojomast/hermes-godseye; Eye Of Hermes research)")
TIMEOUT_S = 12.0
GAP_S = 0.4
# Wikipedia geosearch returns nearest-first. A pin beside a landmark often has a smaller
# sub-feature (e.g. "Pavillon de l'Horloge") closer than the article the pin is named for
# (e.g. "Louvre"), so ask for several hits and rank them instead of trusting hit #1.
GEO_LIMIT = 5
# An exact-title hit further than this from the nearest hit is treated as a distant homonym.
GEO_HOMONYM_FACTOR = 3.0
GEO_HOMONYM_FLOOR_M = 250.0
MAX_HTTP_PER_PIN = 4
MAX_PINS = 24
MAX_IMAGES = 3
MAX_LINKS = 10
MAX_SUMMARY = 900
CACHE_TTL_S = 7 * 24 * 3600
UNAVAILABLE = "_unavailable_"

_lock = threading.Lock()
_last_req = [0.0]
_mem: dict[str, tuple[float, object]] = {}


def data_dir() -> Path:
    from hermes_constants import get_hermes_home
    home = Path(get_hermes_home())
    return home / "plugin-data" / "godseye"


def cache_path() -> Path:
    return data_dir() / "research-cache.json"


def _load_disk() -> dict:
    try:
        return json.loads(cache_path().read_text())
    except Exception:
        return {}


def _save_disk(store: dict) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(store))
        tmp.replace(path)
    except Exception:
        pass


def cache_get(key: str):
    now = time.time()
    hit = _mem.get(key)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    store = _load_disk()
    entry = store.get(key)
    if isinstance(entry, dict) and now - float(entry.get("t") or 0) < CACHE_TTL_S:
        _mem[key] = (float(entry["t"]), entry.get("v"))
        return entry.get("v")
    return None


def cache_set(key: str, value) -> None:
    now = time.time()
    _mem[key] = (now, value)
    store = _load_disk()
    store[key] = {"t": now, "v": value}
    # keep disk cache bounded
    if len(store) > 500:
        oldest = sorted(store.items(), key=lambda kv: float((kv[1] or {}).get("t") or 0))[: len(store) - 400]
        for k, _ in oldest:
            store.pop(k, None)
    _save_disk(store)


def _pace() -> None:
    with _lock:
        wait = GAP_S - (time.monotonic() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.monotonic()


def _http(url: str, timeout: float = TIMEOUT_S, accept: str = "application/json") -> tuple[int, str]:
    from .public_http import UnsafeURL, header_value, request as public_request
    _pace()
    try:
        return public_request(
            url, timeout=timeout, limit=2_000_000,
            headers={"User-Agent": header_value(USER_AGENT), "Accept": accept},
        )
    except UnsafeURL:
        return 0, ""
    except Exception:
        return 0, ""


def _http_url(u) -> str | None:
    u = str(u or "").strip()
    if u and not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I) and re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", u):
        u = "https://" + u
    return u if re.match(r"^https?://[^\s<>\"']+$", u, re.I) else None


def _md(s) -> str:
    return re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", str(s))


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): v for k, v in attrs}
        if tag.lower() == "meta":
            name = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
            content = a.get("content") or ""
            if name and content:
                self.meta[name] = content.strip()
        elif tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip()[:200]


def website_meta(url: str) -> dict:
    """og:description / meta description / og:image from a place website. Fail soft."""
    u = _http_url(url)
    if not u:
        return {"ok": False, "unavailable": True}
    ck = "meta:" + hashlib.sha1(u.encode()).hexdigest()
    hit = cache_get(ck)
    if hit is not None:
        return hit
    from .public_http import get as public_get
    try:
        status, html = public_get(u, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    except (ValueError, OSError, http.client.HTTPException):
        status, html = 0, ""
    if status != 200 or not html:
        out = {"ok": False, "unavailable": True, "url": u, "status": status or None}
        cache_set(ck, out)
        return out
    p = _MetaParser()
    try:
        p.feed(html[:200_000])
    except Exception:
        pass
    desc = (p.meta.get("og:description") or p.meta.get("twitter:description")
            or p.meta.get("description") or None)
    image = _http_url(p.meta.get("og:image") or p.meta.get("twitter:image"))
    out = {"ok": True, "url": u, "title": p.title, "description": (desc or "")[:MAX_SUMMARY] or None,
           "image": image}
    if not out["description"] and not out["image"]:
        out = {"ok": False, "unavailable": True, "url": u, "note": "no meta description or image"}
    cache_set(ck, out)
    return out


def wikipedia_summary(title: str, lang: str = "en") -> dict:
    title = str(title or "").strip()
    lang = lang if re.match(r"^[a-z-]{2,12}$", lang or "") else "en"
    if not title:
        return {"ok": False, "unavailable": True}
    # strip File: etc
    title = title.replace(" ", "_")
    ck = f"wp:{lang}:{title.lower()}"
    hit = cache_get(ck)
    if hit is not None:
        return hit
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title, safe='')}"
    status, body = _http(url)
    if status == 404 or not body:
        out = {"ok": False, "unavailable": True, "title": title, "lang": lang}
        cache_set(ck, out)
        return out
    if status != 200:
        out = {"ok": False, "unavailable": True, "title": title, "lang": lang, "status": status}
        cache_set(ck, out)
        return out
    try:
        data = json.loads(body)
    except Exception:
        out = {"ok": False, "unavailable": True, "title": title}
        cache_set(ck, out)
        return out
    if data.get("type") == "disambiguation":
        out = {"ok": False, "unavailable": True, "title": title, "note": "disambiguation page"}
        cache_set(ck, out)
        return out
    extract = (data.get("extract") or "").strip()[:MAX_SUMMARY] or None
    thumb = None
    src = (data.get("originalimage") or data.get("thumbnail") or {}).get("source")
    if src and src.startswith("https://"):
        thumb = src
    page = (data.get("content_urls") or {}).get("desktop", {}).get("page") or data.get("content_urls", {}).get("page")
    out = {"ok": bool(extract or thumb), "title": data.get("title") or title.replace("_", " "),
           "extract": extract, "image": thumb, "url": page,
           "lang": lang, "description": data.get("description"),
           "unavailable": not bool(extract or thumb)}
    if out["unavailable"]:
        out["ok"] = False
    cache_set(ck, out)
    return out


def _norm_title(value) -> str:
    """Lowercase, unquote and collapse a Wikipedia title/label for comparison."""
    text = urllib.parse.unquote(str(value or "")).replace("_", " ")
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)  # drop a trailing disambiguator
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def _title_score(candidate: str, label: str, title: str) -> float:
    """How well a geosearch title matches the pin label/title; exact beats containment."""
    cand = _norm_title(candidate)
    if not cand:
        return 0.0
    names = {_norm_title(label), _norm_title(title)}
    names.discard("")
    if not names:
        return 0.0
    if cand in names:
        return 100.0
    for name in names:
        if cand in name or name in cand:
            return 70.0
        ct, nt = set(cand.split()), set(name.split())
        if nt and nt <= ct:
            return 60.0
        if ct and nt:
            overlap = len(ct & nt) / len(ct | nt)
            if overlap:
                return 50.0 * overlap
    return 0.0


def _rank_wikipedia_hits(hits: list | None, *, label=None, title=None) -> dict | None:
    """Pick the best geosearch hit: exact pin name first, else best title/distance score.

    Hits beyond a distance horizon around the nearest hit are dropped, so a far-away page
    that merely shares the pin's name cannot beat a closer page with a matching name.
    """
    rows = [h for h in (hits or []) if isinstance(h, dict) and str(h.get("title") or "").strip()]
    if not rows:
        return None

    def dist_of(h):
        try:
            return float(h.get("dist"))
        except (TypeError, ValueError):
            return None

    known = [d for d in (dist_of(h) for h in rows) if d is not None]
    horizon = None
    if known:
        horizon = max(min(known) * GEO_HOMONYM_FACTOR, GEO_HOMONYM_FLOOR_M)
    candidates = []
    for h in rows:
        d = dist_of(h)
        if horizon is None or d is None or d <= horizon:
            candidates.append((h, d))
    if not candidates:
        candidates = [(rows[0], dist_of(rows[0]))]

    names = {_norm_title(label), _norm_title(title)}
    names.discard("")
    exact = [h for h, _ in candidates if _norm_title(h.get("title")) in names]
    if exact:
        return min(exact, key=lambda h: (dist_of(h) is None, dist_of(h) or 0.0))

    def score(pair):
        h, d = pair
        return (_title_score(h.get("title"), label, title) - (d or 0.0) / 1000.0,
                -(d if d is not None else 1e12))

    return max(candidates, key=score)[0]


def wikipedia_geosearch(lat: float, lon: float, lang: str = "en", radius_m: int = 800,
                        limit: int = GEO_LIMIT, prefer_title: str | None = None,
                        label: str | None = None) -> dict:
    """Wikipedia pages near a point via MediaWiki geosearch (keyless).

    ``hits`` keeps the API's nearest-first order; the returned top-level title is the
    best title/label match when ``prefer_title``/``label`` is supplied, else the nearest.
    """
    lang = lang if re.match(r"^[a-z-]{2,12}$", lang or "") else "en"
    limit = max(1, min(20, int(limit)))
    ck = f"geo:{lang}:{round(lat, 4)}:{round(lon, 4)}:{radius_m}:{limit}"
    hit = cache_get(ck)
    if hit is not None:
        cached = hit
    else:
        qs = urllib.parse.urlencode({
            "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}",
            "gsradius": max(50, min(10000, int(radius_m))), "gslimit": limit, "format": "json",
        })
        status, body = _http(f"https://{lang}.wikipedia.org/w/api.php?{qs}")
        hits = []
        if status == 200 and body:
            try:
                data = json.loads(body)
                hits = (data.get("query") or {}).get("geosearch") or []
            except Exception:
                hits = []
        if not hits:
            cached = {"ok": False, "unavailable": True}
        else:
            keep = ("title", "pageid", "dist", "lat", "lon")
            cached = {"ok": True,
                      "title": hits[0].get("title"), "pageid": hits[0].get("pageid"),
                      "dist": hits[0].get("dist"), "lat": hits[0].get("lat"), "lon": hits[0].get("lon"),
                      "hits": [{k: h.get(k) for k in keep if k in h} for h in hits]}
        cache_set(ck, cached)
    if not cached.get("ok"):
        return cached
    best = _rank_wikipedia_hits(cached.get("hits") or [cached], label=label, title=prefer_title)
    out = dict(cached)
    if best:
        for key in ("title", "pageid", "dist", "lat", "lon"):
            if key in best:
                out[key] = best[key]
    return out


def wikidata_entity(qid: str) -> dict:
    qid = str(qid or "").strip().upper()
    if not re.match(r"^Q\d+$", qid):
        return {"ok": False, "unavailable": True}
    ck = f"wd:{qid}"
    hit = cache_get(ck)
    if hit is not None:
        return hit
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    status, body = _http(url)
    if status != 200 or not body:
        out = {"ok": False, "unavailable": True, "qid": qid, "status": status or None}
        cache_set(ck, out)
        return out
    try:
        entity = (json.loads(body).get("entities") or {}).get(qid) or {}
    except Exception:
        out = {"ok": False, "unavailable": True, "qid": qid}
        cache_set(ck, out)
        return out
    labels = entity.get("labels") or {}
    label = (labels.get("en") or next(iter(labels.values()), {}) or {}).get("value")
    descs = entity.get("descriptions") or {}
    description = (descs.get("en") or next(iter(descs.values()), {}) or {}).get("value")
    sitelinks = entity.get("sitelinks") or {}
    enwiki = (sitelinks.get("enwiki") or {}).get("title")
    # P18 image
    image = None
    claims = entity.get("claims") or {}
    p18 = claims.get("P18") or []
    if p18:
        try:
            fname = p18[0]["mainsnak"]["datavalue"]["value"]
            image = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                     + urllib.parse.quote(fname.replace(" ", "_")) + "?width=640")
        except Exception:
            pass
    out = {"ok": True, "qid": qid, "label": label, "description": description,
           "enwiki": enwiki, "image": image, "url": f"https://www.wikidata.org/wiki/{qid}"}
    cache_set(ck, out)
    return out


def commons_search(query: str, limit: int = 2) -> list[dict]:
    q = str(query or "").strip()
    if not q:
        return []
    ck = f"commons:{q.lower()}:{limit}"
    hit = cache_get(ck)
    if hit is not None:
        return hit if isinstance(hit, list) else []
    qs = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {q}", "gsrlimit": max(1, min(5, limit)),
        "gsrnamespace": 6, "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 640,
    })
    status, body = _http(f"https://commons.wikimedia.org/w/api.php?{qs}")
    out: list[dict] = []
    if status == 200 and body:
        try:
            pages = (json.loads(body).get("query") or {}).get("pages") or {}
            for pg in pages.values():
                info = (pg.get("imageinfo") or [{}])[0]
                url = info.get("thumburl") or info.get("url")
                if url and str(url).startswith("https://") and str(info.get("mime") or "").startswith("image/"):
                    out.append({"url": url, "alt": (pg.get("title") or "Commons").replace("File:", "")[:120],
                                "caption": "Wikimedia Commons"})
        except Exception:
            out = []
    cache_set(ck, out)
    return out[:limit]


def _qid_from_pin(spec: dict) -> str | None:
    for link in spec.get("links") or []:
        if not isinstance(link, dict):
            continue
        u = str(link.get("url") or "")
        m = re.search(r"wikidata\.org/wiki/(Q\d+)", u, re.I)
        if m:
            return m.group(1).upper()
    md = str(spec.get("description_md") or "")
    m = re.search(r"\b(Q\d+)\b", md)
    return m.group(1).upper() if m else None


def _wiki_from_pin(spec: dict) -> tuple[str, str] | None:
    for link in spec.get("links") or []:
        if not isinstance(link, dict):
            continue
        u = str(link.get("url") or "")
        m = re.match(r"https?://([a-z-]{2,12})\.wikipedia\.org/wiki/([^?#]+)", u, re.I)
        if m:
            return m.group(1).lower(), urllib.parse.unquote(m.group(2))
    return None


def _website_from_pin(spec: dict) -> str | None:
    for link in spec.get("links") or []:
        if isinstance(link, dict) and str(link.get("label") or "").lower() in ("website", "site", "home", "homepage"):
            u = _http_url(link.get("url"))
            if u:
                return u
    for link in spec.get("links") or []:
        if not isinstance(link, dict):
            continue
        u = _http_url(link.get("url"))
        if not u:
            continue
        host = urllib.parse.urlparse(u).hostname or ""
        if host and "wikipedia.org" not in host and "wikidata.org" not in host and "openstreetmap.org" not in host:
            return u
    return None


def research_one(spec: dict, budget: list[int] | None = None) -> dict:
    """Gather research for one pin spec. ``budget`` is a mutable [remaining_http] counter."""
    budget = budget if budget is not None else [MAX_HTTP_PER_PIN]
    label = str(spec.get("label") or spec.get("title") or spec.get("target") or "Pin")[:120]
    title = str(spec.get("title") or label)[:120]
    lat, lon = spec.get("latitude"), spec.get("longitude")
    sources: list[dict] = []
    images: list[dict] = []
    links: list[dict] = []
    parts: list[str] = []
    status_bits: list[str] = []
    used = 0

    def spend() -> bool:
        nonlocal used
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        used += 1
        return True

    def add_image(url, alt, caption=None):
        u = _http_url(url)
        if not u or any(i["url"] == u for i in images):
            return
        if len(images) >= MAX_IMAGES:
            return
        images.append({"url": u, "alt": str(alt or label)[:120], **({"caption": caption} if caption else {})})

    def add_link(url, lab):
        u = _http_url(url)
        if not u or any(l["url"] == u for l in links):
            return
        if len(links) >= MAX_LINKS:
            return
        links.append({"url": u, "label": str(lab or u)[:80]})

    # Keep existing OSM/drawer content as a baseline (already-fetched facts).
    existing_md = str(spec.get("description_md") or "").strip()
    if existing_md and "Sources" not in existing_md.split("\n")[0:1]:
        # preserve OSM block when present and not already a research write
        if not existing_md.startswith("### Research"):
            parts.append(existing_md)
            sources.append({"name": "OpenStreetMap tags (existing pin)", "status": "kept"})
    for im in spec.get("images") or []:
        if isinstance(im, dict):
            add_image(im.get("url"), im.get("alt") or label, im.get("caption"))
    for lk in spec.get("links") or []:
        if isinstance(lk, dict):
            add_link(lk.get("url"), lk.get("label"))

    # Wikidata
    qid = _qid_from_pin(spec)
    wd = None
    if qid and spend():
        wd = wikidata_entity(qid)
        if wd.get("ok"):
            if wd.get("description"):
                parts.append(f"**Wikidata:** {_md(wd['description'])}")
            if wd.get("image"):
                add_image(wd["image"], wd.get("label") or label, "Wikidata P18")
            add_link(wd["url"], "Wikidata")
            sources.append({"name": "Wikidata", "url": wd["url"], "status": "ok"})
            status_bits.append("wikidata")
        else:
            sources.append({"name": "Wikidata", "status": "unavailable", "qid": qid})
    elif qid:
        sources.append({"name": "Wikidata", "status": "skipped_budget", "qid": qid})

    # Wikipedia (from pin link, wikidata sitelink, then geosearch, then label)
    wiki_ref = _wiki_from_pin(spec)
    wp = None
    if wiki_ref and spend():
        wp = wikipedia_summary(wiki_ref[1], wiki_ref[0])
    elif wd and wd.get("enwiki") and spend():
        wp = wikipedia_summary(wd["enwiki"], "en")
    elif isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and spend():
        geo = wikipedia_geosearch(float(lat), float(lon), prefer_title=title, label=label)
        picked = _rank_wikipedia_hits(geo.get("hits") or ([geo] if geo.get("ok") else []),
                                      label=label, title=title)
        geo_title = (picked or {}).get("title") or geo.get("title")
        geo_dist = (picked or {}).get("dist", geo.get("dist"))
        if geo.get("ok") and geo_title and spend():
            wp = wikipedia_summary(geo_title, "en")
            if wp.get("ok") and geo_dist is not None:
                sources.append({"name": "Wikipedia geosearch", "status": "ok",
                                "dist_m": geo_dist, "title": geo_title})
        elif not geo.get("ok"):
            sources.append({"name": "Wikipedia geosearch", "status": "unavailable"})
    elif spend():
        wp = wikipedia_summary(label, "en")

    if wp is not None:
        if wp.get("ok") and wp.get("extract"):
            parts.append(wp["extract"])
            if wp.get("url"):
                add_link(wp["url"], "Wikipedia")
            if wp.get("image"):
                add_image(wp["image"], wp.get("title") or label, "Wikipedia")
            sources.append({"name": "Wikipedia", "url": wp.get("url"), "status": "ok",
                            "title": wp.get("title")})
            status_bits.append("wikipedia")
        else:
            sources.append({"name": "Wikipedia", "status": "unavailable",
                            "note": wp.get("note") or "no summary"})

    # Commons (only if we still have room for images and budget)
    if len(images) < MAX_IMAGES and spend():
        for im in commons_search(label, limit=MAX_IMAGES - len(images)):
            add_image(im["url"], im.get("alt"), im.get("caption"))
        if any(i.get("caption") == "Wikimedia Commons" for i in images):
            sources.append({"name": "Wikimedia Commons", "status": "ok"})
            status_bits.append("commons")
        else:
            sources.append({"name": "Wikimedia Commons", "status": "unavailable"})

    # Website meta
    site = _website_from_pin(spec)
    if site and spend():
        meta = website_meta(site)
        if meta.get("ok"):
            if meta.get("description"):
                parts.append(f"**Website:** {_md(meta['description'])}")
            if meta.get("image"):
                add_image(meta["image"], label, "Website og:image")
            add_link(site, "Website")
            sources.append({"name": "Website meta", "url": site, "status": "ok"})
            status_bits.append("website")
        else:
            sources.append({"name": "Website meta", "url": site, "status": "unavailable",
                            "note": meta.get("note")})
    elif site:
        sources.append({"name": "Website meta", "url": site, "status": "skipped_budget"})

    if not parts:
        parts.append("_No research summary available for this pin._")

    # Sources section (markdown) — always present; marks unavailable explicitly
    src_lines = ["### Sources"]
    for s in sources:
        bit = f"- **{s['name']}**: {s.get('status', 'unknown')}"
        if s.get("url"):
            bit += f" — [{s['url']}]({s['url']})"
        if s.get("note"):
            bit += f" ({_md(s['note'])})"
        if s.get("dist_m") is not None:
            bit += f" ({s['dist_m']} m)"
        src_lines.append(bit)
    if not sources:
        src_lines.append(f"- _{UNAVAILABLE}: no free sources returned data_")
    # Agent web-search note
    src_lines.append(
        "- _Agent web search:_ this plugin cannot call Hermes `web_search`; "
        "pass verified findings via `agent_notes` on `godseye_research_pins`.")

    description_md = "\n\n".join(parts + ["\n".join(src_lines)])[:4000]
    return {
        "ok": True,
        "label": label,
        "title": title,
        "description_md": description_md,
        "images": images[:MAX_IMAGES],
        "links": links[:MAX_LINKS],
        "sources": sources,
        "http_used": used,
        "filled": bool(status_bits),
        "channels": status_bits,
    }


def _match_pins(annotations: list[dict], ids=None, labels=None) -> list[dict]:
    """annotations are SceneStore entries {id, spec, paneId?}."""
    want_ids = {str(x) for x in (ids or []) if x}
    want_labels = {str(x).lower() for x in (labels or []) if x}
    out = []
    for a in annotations:
        spec = a.get("spec") or {}
        t = str(spec.get("type") or "pin").lower()
        if t not in ("pin", "marker", "point", "highlight", "card"):
            continue
        if want_ids or want_labels:
            lab = str(spec.get("label") or spec.get("title") or "").lower()
            if a.get("id") not in want_ids and lab not in want_labels and not any(
                    lab.startswith(w) or w in lab for w in want_labels):
                # also allow paneId match
                if a.get("paneId") not in want_ids:
                    continue
        out.append(a)
        if len(out) >= MAX_PINS:
            break
    return out


def apply_agent_notes(result: dict, note: dict) -> dict:
    """Merge agent-supplied verified notes into a research result (never invent)."""
    if not isinstance(note, dict):
        return result
    summary = str(note.get("summary") or "").strip()
    if summary:
        # insert before Sources
        md = result.get("description_md") or ""
        if "### Sources" in md:
            head, tail = md.split("### Sources", 1)
            md = head.rstrip() + "\n\n**Agent notes (verified search):** " + _md(summary) + "\n\n### Sources" + tail
        else:
            md = md + "\n\n**Agent notes (verified search):** " + _md(summary)
        result["description_md"] = md[:4000]
        result.setdefault("sources", []).append({"name": "Agent web search", "status": "ok"})
        result["channels"] = list(result.get("channels") or []) + ["agent"]
        result["filled"] = True
    for im in note.get("images") or []:
        if isinstance(im, dict) and _http_url(im.get("url")):
            if len(result.setdefault("images", [])) < MAX_IMAGES:
                result["images"].append({"url": _http_url(im["url"]),
                                         "alt": str(im.get("alt") or result.get("label") or "")[:120]})
    for lk in note.get("links") or []:
        if isinstance(lk, dict) and _http_url(lk.get("url")):
            if len(result.setdefault("links", [])) < MAX_LINKS:
                result["links"].append({"url": _http_url(lk["url"]),
                                        "label": str(lk.get("label") or lk["url"])[:80]})
    return result


def research_pins(scene_store, session_key: str | None, *, ids=None, labels=None,
                  all_pins: bool = False, agent_notes=None, live=None, persist: bool = True,
                  mode: str = "full") -> dict:
    """Research pins in the current scene; write drawer fields back and optionally redraw.

    mode=full (default for this tool): Wikipedia/Wikidata/Commons/website meta.
    mode=light: intent-shaped local facts only (via enrich.ensure_drawer).
    """
    listing = scene_store.list(session_key)
    anns = scene_store.annotations(session_key)
    if ids or labels:
        targets = _match_pins(anns, ids, labels)
    else:
        targets = _match_pins(anns, None, None)[:MAX_PINS]

    if not targets:
        return {"ok": False, "error": "no_pins", "message": "No matching pins in the current scene.",
                "scene": listing.get("scene"), "annotations": listing.get("annotations")}

    notes_by: dict[str, dict] = {}
    for n in agent_notes or []:
        if not isinstance(n, dict):
            continue
        key = str(n.get("id") or n.get("label") or "").lower()
        if key:
            notes_by[key] = n

    results = []
    updated = 0
    for a in targets:
        spec = dict(a.get("spec") or {})
        try:
            if mode == "light":
                from . import enrich
                filled = enrich.ensure_drawer(spec, intent=(agent_notes or [None])[0].get("intent")
                                             if agent_notes and isinstance(agent_notes[0], dict) else None,
                                             mode="light")
                one = {"ok": True, "label": filled.get("label") or filled.get("title"),
                       "title": filled.get("title"), "description_md": filled.get("description_md"),
                       "images": filled.get("images") or [], "links": filled.get("links") or [],
                       "sources": [{"name": "local tags", "status": "ok"}], "http_used": 0,
                       "filled": enrich.has_drawer(filled), "channels": ["light"]}
            else:
                one = research_one(spec)
        except Exception as exc:
            one = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                   "label": spec.get("label"), "unavailable": True}
            results.append({"id": a.get("id"), **one})
            continue
        note = notes_by.get(str(a.get("id") or "").lower()) or notes_by.get(
            str(spec.get("label") or "").lower())
        if note:
            one = apply_agent_notes(one, note)
        # write into scene store
        patch = {"title": one.get("title") or spec.get("title") or spec.get("label"),
                 "description_md": one.get("description_md"),
                 "images": one.get("images") or [],
                 "links": one.get("links") or []}
        if scene_store.update_annotation(session_key, a["id"], patch):
            updated += 1
        results.append({"id": a.get("id"), "label": one.get("label"), "ok": one.get("ok", True),
                        "filled": one.get("filled"), "channels": one.get("channels"),
                        "http_used": one.get("http_used"), "sources": one.get("sources"),
                        "images": len(one.get("images") or []), "links": len(one.get("links") or [])})

    pane = None
    if persist and updated and live is not None:
        # Redraw scene so drawers show new content. Snapshot the specs BEFORE clearing: a
        # live relay records clear_annotations into the same scene store, which would
        # otherwise leave nothing to redraw (and lose every pin).
        try:
            with scene_store.lock:
                before = [{"id": x["id"], "paneId": x.get("paneId"), "spec": dict(x["spec"])}
                          for x in scene_store.scene(session_key).get("annotations") or []]
            pane = live("clear_annotations", {})
            for i in range(0, len(before), 24):
                live("annotate_map", {"annotations": [x["spec"] for x in before[i:i + 24]],
                                      "persist": True})
            # A live relay can record the redraw into this same store, replacing every
            # annotation id. Research only owns drawer fields, so put the pin ids back.
            with scene_store.lock:
                sc = scene_store.scene(session_key)
                anns = sc.get("annotations") or []
                if not anns and before:
                    sc["annotations"] = before
                elif len(anns) == len(before):
                    for ann, old in zip(anns, before):
                        ann["id"] = old["id"]
                sc["updated"] = time.time()
                save = getattr(scene_store, "_save", None)
                if callable(save):
                    save()
        except Exception as exc:
            pane = {"ok": False, "error": str(exc)[:200]}

    return {"ok": True, "scene": listing.get("scene"), "researched": len(results),
            "updated": updated, "pins": results, "pane": pane,
            "cache": str(cache_path()),
            "note": "Missing channels are marked unavailable; facts are never invented. "
                    "Use agent_notes with Hermes web_search results when free sources are thin."}
