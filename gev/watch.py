"""Area watches: check feeds over an area on an interval and alert on NEW items only.

A watch = an area (bbox / polygon / center+radius / the pane's current view, resolved at create
time), feeds (earthquakes, fires, flights, ships, pois), per-feed conditions and an interval.
Nothing is created automatically and the background loop only runs while an active watch exists.

Scheduling: Hermes' cron (cron/jobs.py) schedules *agent prompts* (a full model turn per run); there
is no plugin API to register a plain Python callback. A watch check is a cheap HTTP poll, so it runs
in a lightweight daemon thread here, with last-seen ids persisted so restarts never re-alert.

Delivery: every alert is queued (persisted) and handed to the next chat turn through the
``pre_llm_call`` hook. With deliver="inject" the plugin also calls ``ctx.inject_message`` (Hermes'
plugin message injection) to start a turn in the watch's chat right away; Hermes only accepts that
when ``plugins.entries.godseye.allow_gateway_injection: true`` is set, otherwise the queue is used.
Each new item is also pinned on the open pane with drawer details.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

FEEDS = ("earthquakes", "fires", "flights", "ships", "pois")
FEED_KEYS = {"fires": "FIRMS_MAP_KEY", "ships": "AISSTREAM_API_KEY"}
MIN_INTERVAL_S, DEFAULT_INTERVAL_S, MAX_INTERVAL_S = 60, 900, 7 * 86400
SEEN_CAP = 3000
MAX_ALERTS_QUEUED = 200
MAX_PINS_PER_RUN = 12
COLORS = {"earthquakes": "amber", "fires": "red", "flights": "blue", "ships": "cyan", "pois": "green"}
TICK_S = 15.0


def default_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    return home / "plugin-data" / "godseye" / "watches.json"


# ---- geometry -------------------------------------------------------------------------------
def _num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi and math.isfinite(x) else None


def normalize_area(area: dict) -> dict:
    """-> {kind, bbox:[s,w,n,e], ...}. Raises ValueError with a clear message."""
    if not isinstance(area, dict):
        raise ValueError("area must be an object: {bbox:[south,west,north,east]} | {polygon:[[lat,lon],...]} | "
                         "{lat, lon, radius_km} | {view: true}")
    if area.get("bbox") is not None:
        b = area["bbox"]
        if not isinstance(b, (list, tuple)) or len(b) != 4:
            raise ValueError("bbox must be [south, west, north, east]")
        s, w, n, e = (_num(b[0], -90, 90), _num(b[1], -180, 180), _num(b[2], -90, 90), _num(b[3], -180, 180))
        if None in (s, w, n, e) or s >= n or w >= e:
            raise ValueError("bbox must be [south, west, north, east] with south<north and west<east")
        return {"kind": "bbox", "bbox": [s, w, n, e]}
    if area.get("polygon") is not None:
        pts = []
        for p in area["polygon"] if isinstance(area["polygon"], list) else []:
            if isinstance(p, dict):
                la, lo = _num(p.get("lat", p.get("latitude")), -90, 90), _num(p.get("lon", p.get("longitude")), -180, 180)
            elif isinstance(p, (list, tuple)) and len(p) == 2:
                la, lo = _num(p[0], -90, 90), _num(p[1], -180, 180)
            else:
                la = lo = None
            if la is None or lo is None:
                raise ValueError("polygon points must be [lat, lon] or {lat, lon}")
            pts.append([la, lo])
        if not 3 <= len(pts) <= 200:
            raise ValueError("polygon needs 3-200 points")
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        return {"kind": "polygon", "polygon": pts, "bbox": [min(lats), min(lons), max(lats), max(lons)]}
    la, lo = _num(area.get("lat", area.get("latitude")), -90, 90), _num(area.get("lon", area.get("longitude")), -180, 180)
    r = _num(area.get("radius_km"), 0.1, 2000)
    if la is not None and lo is not None:
        if r is None:
            raise ValueError("center areas need radius_km (0.1-2000)")
        dlat = r / 111.32
        dlon = r / max(1e-6, 111.32 * math.cos(math.radians(la)))
        return {"kind": "circle", "lat": la, "lon": lo, "radius_km": r,
                "bbox": [max(-90, la - dlat), max(-180, lo - dlon), min(90, la + dlat), min(180, lo + dlon)]}
    raise ValueError("area needs bbox, polygon, lat/lon+radius_km, or view=true")


def haversine_km(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def _in_poly(lat, lon, pts) -> bool:
    inside, j = False, len(pts) - 1
    for i in range(len(pts)):
        yi, xi = pts[i]
        yj, xj = pts[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def contains(area: dict, lat, lon) -> bool:
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False
    s, w, n, e = area["bbox"]
    if not (s <= lat <= n and w <= lon <= e):
        return False
    if area["kind"] == "circle":
        return haversine_km(area["lat"], area["lon"], lat, lon) <= area["radius_km"]
    if area["kind"] == "polygon":
        return _in_poly(lat, lon, area["polygon"])
    return True


def view_area(view: dict) -> dict:
    """Current-view area from godseye_live_get_current_view_state output (bbox or center+range)."""
    r = view.get("result") if isinstance(view.get("result"), dict) else view
    for k in ("bbox", "viewBbox", "bounds", "rectangle"):
        b = r.get(k)
        if isinstance(b, dict) and all(x in b for x in ("south", "west", "north", "east")):
            return normalize_area({"bbox": [b["south"], b["west"], b["north"], b["east"]]})
        if isinstance(b, list) and len(b) == 4:  # upstream bbox order is [west, south, east, north]
            return normalize_area({"bbox": [b[1], b[0], b[3], b[2]]})
    cam = r.get("camera") if isinstance(r.get("camera"), dict) else r
    la = cam.get("targetLat", cam.get("latitude"))
    lo = cam.get("targetLon", cam.get("longitude"))
    rng = cam.get("rangeM") or cam.get("heightM") or cam.get("altitudeM")
    if isinstance(la, (int, float)) and isinstance(lo, (int, float)) and isinstance(rng, (int, float)):
        return normalize_area({"lat": la, "lon": lo, "radius_km": max(0.5, min(2000, rng / 1000.0))})
    raise ValueError("could not read the current view (open the EOH pane, or pass bbox/polygon/center)")


# ---- keys (presence and value; values never logged or returned) -------------------------------
def key_value(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    try:
        from .composer import env_file, read_env
        return read_env(env_file()).get(name) or None
    except Exception:
        return None


# ---- feeds: each returns {"status": "OK"|"KEY_MISSING"|"ERROR", "items": [...], "error"?} ------
# item = {id, lat, lon, title, description_md, links[], time?}
def _get(url: str, timeout: float = 20.0) -> tuple[int, str]:
    from . import poi
    return poi._http(url, timeout=timeout)


def _mag(m) -> str:
    return f"{m:.1f}" if isinstance(m, (int, float)) else "?"


def feed_earthquakes(area: dict, cond: dict) -> dict:
    s, w, n, e = area["bbox"]
    start = cond.get("start") or cond.get("starttime")
    end = cond.get("end") or cond.get("endtime")
    if not start:
        start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 86400 * float(cond.get("lookback_days", 1))))
    q = {"format": "geojson", "minlatitude": s, "minlongitude": w, "maxlatitude": n, "maxlongitude": e,
         "minmagnitude": float(cond.get("min_magnitude", 2.5)), "orderby": "time", "limit": 200,
         "starttime": str(start)[:19]}
    if end:
        q["endtime"] = str(end)[:19]
    code, body = _get("https://earthquake.usgs.gov/fdsnws/event/1/query?" + urllib.parse.urlencode(q))
    if code != 200:
        return {"status": "ERROR", "items": [], "error": f"USGS HTTP {code}"}
    items = []
    for f in json.loads(body).get("features", []):
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
        if len(g) < 2:
            continue
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime((p.get("time") or 0) / 1000))
        items.append({"id": f"usgs:{f.get('id')}", "lat": g[1], "lon": g[0], "time": when,
                      "title": f"M{_mag(p.get('mag'))} {p.get('place') or 'earthquake'}"[:120],
                      "description_md": f"**Magnitude:** {_mag(p.get('mag'))}\n**Depth:** {g[2] if len(g) > 2 else '?'} km\n"
                                        f"**Time:** {when}\n\nSource: USGS",
                      "links": [{"label": "USGS event", "url": p["url"]}] if str(p.get("url", "")).startswith("https://") else []})
    return {"status": "OK", "items": items}


def feed_fires(area: dict, cond: dict) -> dict:
    key = key_value("FIRMS_MAP_KEY")
    if not key:
        return {"status": "KEY_MISSING", "items": [], "key": "FIRMS_MAP_KEY",
                "error": "FIRMS_MAP_KEY is not set (env or ~/.config/gods-eye-view.env); fires are not checked. "
                         "Get a free key at https://firms.modaps.eosdis.nasa.gov/api/map_key/"}
    s, w, n, e = area["bbox"]
    src = str(cond.get("source") or "VIIRS_SNPP_NRT")
    try:
        days = int(cond.get("days") or 1)
    except (TypeError, ValueError):
        days = 1
    days = max(1, min(10, days))
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{urllib.parse.quote(key)}/{src}/{w},{s},{e},{n}/{days}"
    try:
        code, body = _get(url)
    except Exception as exc:  # never echo the URL (it contains the key)
        return {"status": "ERROR", "items": [], "error": f"FIRMS request failed: {type(exc).__name__}"}
    if code != 200 or body.lstrip().lower().startswith(("invalid", "error")):
        return {"status": "ERROR", "items": [], "error": f"FIRMS HTTP {code}"}
    items, min_frp = [], float(cond.get("min_frp_mw", 0) or 0)
    conf_ok = {str(c).lower() for c in cond.get("confidence", [])} if cond.get("confidence") else None
    for r in csv.DictReader(io.StringIO(body)):
        try:
            la, lo = float(r["latitude"]), float(r["longitude"])
        except (KeyError, ValueError):
            continue
        frp = float(r.get("frp") or 0)
        if frp < min_frp or (conf_ok and str(r.get("confidence", "")).lower() not in conf_ok):
            continue
        when = f"{r.get('acq_date', '')} {r.get('acq_time', '')}".strip()
        items.append({"id": f"firms:{round(la, 4)},{round(lo, 4)},{when}", "lat": la, "lon": lo, "time": when,
                      "title": f"Fire hotspot ({frp:g} MW)",
                      "description_md": f"**FRP:** {frp:g} MW\n**Confidence:** {r.get('confidence', '?')}\n"
                                        f"**Detected:** {when} UTC\n**Satellite:** {r.get('satellite', src)}\n\nSource: NASA FIRMS",
                      "links": [{"label": "FIRMS map", "url": f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lo:.3f},{la:.3f},10z"}]})
    return {"status": "OK", "items": items}


def feed_flights(area: dict, cond: dict) -> dict:
    s, w, n, e = area["bbox"]
    code, body = _get(f"https://opensky-network.org/api/states/all?lamin={s}&lomin={w}&lamax={n}&lomax={e}")
    if code != 200:
        return {"status": "ERROR", "items": [], "error": f"OpenSky HTTP {code} (anonymous access is rate-limited)"}
    prefix = [str(p).upper() for p in (cond.get("callsign_prefix") or [])] if isinstance(cond.get("callsign_prefix"), list) \
        else ([str(cond["callsign_prefix"]).upper()] if cond.get("callsign_prefix") else [])
    min_alt, max_alt = cond.get("min_altitude_m"), cond.get("max_altitude_m")
    items = []
    for st in json.loads(body).get("states") or []:
        icao, cs, la, lo, alt, ground = st[0], (st[1] or "").strip(), st[6], st[5], st[7], st[8]
        if ground and cond.get("airborne_only", True):
            continue
        if prefix and not any(cs.upper().startswith(p) for p in prefix):
            continue
        if min_alt is not None and (alt is None or alt < float(min_alt)):
            continue
        if max_alt is not None and (alt is None or alt > float(max_alt)):
            continue
        items.append({"id": f"icao:{icao}", "lat": la, "lon": lo, "title": f"Flight {cs or icao}",
                      "description_md": f"**Callsign:** {cs or '?'}\n**ICAO24:** `{icao}`\n**Origin:** {st[2]}\n"
                                        f"**Altitude:** {alt if alt is not None else '?'} m\n\nSource: OpenSky Network",
                      "links": [{"label": "ADS-B Exchange", "url": f"https://globe.adsbexchange.com/?icao={icao}"}]})
    return {"status": "OK", "items": items}


def feed_ships(area: dict, cond: dict) -> dict:
    if not key_value("AISSTREAM_API_KEY"):
        return {"status": "KEY_MISSING", "items": [], "key": "AISSTREAM_API_KEY",
                "error": "AISSTREAM_API_KEY is not set (env or ~/.config/gods-eye-view.env); ships are not checked."}
    s, w, n, e = area["bbox"]
    try:  # the app holds the AIS stream; ask it (needs the God's Eye View MCP server)
        from . import composer
        res = composer.mcp_call("vessels_in_area", {"area": {"bbox": [w, s, e, n]}, "limit": 200,
                                                     **({"type": cond["type"]} if cond.get("type") else {})})
    except Exception as exc:
        return {"status": "ERROR", "items": [], "error": f"vessels_in_area failed: {type(exc).__name__}: {str(exc)[:160]}"}
    data = (res or {}).get("structuredContent") or {}
    rows = next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), []) if isinstance(data, dict) else []
    items = []
    for r in rows:
        mmsi = r.get("mmsi") or r.get("MMSI")
        la, lo = r.get("lat", r.get("latitude")), r.get("lon", r.get("longitude"))
        if not mmsi or not isinstance(la, (int, float)):
            continue
        name = r.get("name") or r.get("shipName") or f"MMSI {mmsi}"
        items.append({"id": f"mmsi:{mmsi}", "lat": la, "lon": lo, "title": f"Ship {name}"[:120],
                      "description_md": f"**MMSI:** `{mmsi}`\n**Type:** {r.get('type') or '?'}\n**Destination:** {r.get('destination') or '?'}\n\nSource: AISStream",
                      "links": [{"label": "MarineTraffic", "url": f"https://www.marinetraffic.com/en/ais/details/ships/mmsi:{mmsi}"}]})
    return {"status": "OK", "items": items}


def feed_pois(area: dict, cond: dict) -> dict:
    from . import poi
    if not cond.get("category") and not cond.get("cuisine"):
        return {"status": "ERROR", "items": [], "error": "pois feed needs conditions.pois.category or .cuisine"}
    out = poi.find_places({"bbox": area["bbox"], "category": cond.get("category"), "cuisine": cond.get("cuisine"), "limit": 100})
    if not out.get("ok"):
        return {"status": "ERROR", "items": [], "error": str(out.get("error") or out.get("code"))[:200]}
    items = []
    for p in out.get("places", []):
        d = poi.drawer_fields(p)
        items.append({"id": f"osm:{p.get('osm') or (p['name'], p['lat'], p['lon'])}", "lat": p["lat"], "lon": p["lon"],
                      "title": p["name"][:120], "description_md": d.get("description_md", ""), "links": d.get("links", []),
                      "tags": poi.pin_tags(p)})
    return {"status": "OK", "items": items}


FETCHERS = {"earthquakes": feed_earthquakes, "fires": feed_fires, "flights": feed_flights, "ships": feed_ships, "pois": feed_pois}


# ---- store + runner -----------------------------------------------------------------------
class WatchStore:
    def __init__(self, path: Path | None = None, fetchers: dict | None = None, pin=None, deliver=None,
                 clock=time.time) -> None:
        self.path = path
        self.fetchers = fetchers or FETCHERS
        self.pin = pin            # callable(session_id, annotations) -> dict
        self.deliver = deliver    # callable(watch, text) -> bool  (immediate delivery, e.g. inject_message)
        self.clock = clock
        self.lock = threading.RLock()
        self.data = {"watches": {}, "alerts": []}
        self._thread = None
        self._stop = threading.Event()
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text())
                self.data.setdefault("watches", {})
                self.data.setdefault("alerts", [])
            except Exception:
                pass

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
        except Exception:
            pass

    # -- CRUD ---------------------------------------------------------------------------------
    def create(self, args: dict, session_id: str | None = None, view_fn=None) -> dict:
        area_in = args.get("area") or {}
        try:
            if isinstance(area_in, dict) and area_in.get("view") or area_in == "view":
                if not view_fn:
                    raise ValueError("view areas need the EOH pane open")
                area = view_area(view_fn())
            else:
                area = normalize_area(area_in)
        except ValueError as exc:
            return {"ok": False, "code": "BAD_AREA", "error": str(exc)}
        feeds = [str(f).lower() for f in (args.get("feeds") or [])]
        bad = [f for f in feeds if f not in FEEDS]
        if not feeds or bad:
            return {"ok": False, "code": "BAD_FEEDS", "error": f"feeds must be a non-empty subset of {list(FEEDS)}"
                    + (f"; unknown: {bad}" if bad else "")}
        interval = int(max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, float(args.get("interval_s") or DEFAULT_INTERVAL_S))))
        wid = "w-" + uuid.uuid4().hex[:8]
        w = {"id": wid, "name": str(args.get("name") or f"Watch {wid}")[:80], "area": area, "feeds": feeds,
             "conditions": args.get("conditions") if isinstance(args.get("conditions"), dict) else {},
             "interval_s": interval, "active": True, "session_id": str(session_id) if session_id else None,
             "deliver": "inject" if args.get("deliver") == "inject" else "queue",
             "pin": args.get("pin", True) is not False, "baseline": args.get("baseline", True) is not False,
             "created": self.clock(), "last_run": None, "next_run": self.clock(), "seen": {}, "status": {}}
        with self.lock:
            self.data["watches"][wid] = w
            self._save()
        keys = {f: FEED_KEYS[f] for f in feeds if f in FEED_KEYS and not key_value(FEED_KEYS[f])}
        self.ensure_running()
        return {"ok": True, "watch": self.public(w),
                **({"key_missing": keys, "warning": "These feeds will report KEY_MISSING until the key is set: "
                    + ", ".join(f"{f} ({k})" for f, k in keys.items())} if keys else {})}

    @staticmethod
    def public(w: dict) -> dict:
        return {k: v for k, v in w.items() if k != "seen"} | {"seen_counts": {f: len(v) for f, v in w.get("seen", {}).items()}}

    def list(self, session_id: str | None = None) -> dict:
        with self.lock:
            ws = [self.public(w) for w in self.data["watches"].values()]
            pend = [a for a in self.data["alerts"] if not a.get("delivered")]
            return {"ok": True, "watches": ws, "pending_alerts": len(pend), "loop_running": self.running()}

    def set_active(self, wid: str, active: bool) -> dict:
        with self.lock:
            w = self.data["watches"].get(wid)
            if not w:
                return {"ok": False, "code": "NOT_FOUND", "error": f"no watch {wid!r}"}
            w["active"] = bool(active)
            if active:
                w["next_run"] = self.clock()
            self._save()
        if active:
            self.ensure_running()
        return {"ok": True, "watch": self.public(w)}

    def delete(self, wid: str, confirm: bool) -> dict:
        with self.lock:
            w = self.data["watches"].get(wid)
            if not w:
                return {"ok": False, "code": "NOT_FOUND", "error": f"no watch {wid!r}"}
            if confirm is not True:
                return {"ok": False, "code": "CONFIRM_REQUIRED",
                        "error": f"Deleting {w['name']!r} drops its last-seen state. Ask the user, then call again with confirm=true."}
            del self.data["watches"][wid]
            self._save()
        return {"ok": True, "deleted": wid}

    # -- checking -----------------------------------------------------------------------------
    def check(self, wid: str) -> dict:
        """Run one watch now: fetch, filter to the area, alert on unseen ids, pin them."""
        with self.lock:
            w = self.data["watches"].get(wid)
            if not w:
                return {"ok": False, "code": "NOT_FOUND", "error": f"no watch {wid!r}"}
            w = json.loads(json.dumps(w))
        first = w["last_run"] is None
        report, new_items = {}, []
        for feed in w["feeds"]:
            cond = (w.get("conditions") or {}).get(feed) or {}
            try:
                res = self.fetchers[feed](w["area"], cond)
            except Exception as exc:
                res = {"status": "ERROR", "items": [], "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            items = [i for i in res.get("items", []) if contains(w["area"], i.get("lat"), i.get("lon"))]
            seen = w["seen"].setdefault(feed, [])
            seen_set = set(seen)
            fresh = [i for i in items if i["id"] not in seen_set]
            for i in fresh:
                seen.append(i["id"])
                seen_set.add(i["id"])
            del seen[:-SEEN_CAP]
            baseline = first and w.get("baseline", True)
            report[feed] = {"status": res.get("status", "OK"), "in_area": len(items),
                            "new": 0 if baseline else len(fresh), **({"baselined": len(fresh)} if baseline else {}),
                            **({"error": res["error"]} if res.get("error") else {}), **({"key": res["key"]} if res.get("key") else {})}
            if not baseline:
                new_items += [{**i, "feed": feed} for i in fresh]
        now = self.clock()
        with self.lock:
            cur = self.data["watches"].get(wid)
            if not cur:
                return {"ok": False, "code": "NOT_FOUND", "error": "deleted during check"}
            cur["seen"], cur["last_run"], cur["status"] = w["seen"], now, report
            cur["next_run"] = now + cur["interval_s"]
            alert = None
            if new_items:
                alert = {"id": "al-" + uuid.uuid4().hex[:8], "watch": wid, "name": cur["name"], "time": now,
                         "session_id": cur.get("session_id"), "items": [
                             {k: i.get(k) for k in ("feed", "id", "title", "lat", "lon", "time")} for i in new_items[:50]],
                         "count": len(new_items), "delivered": False}
                self.data["alerts"].append(alert)
                del self.data["alerts"][:-MAX_ALERTS_QUEUED]
            self._save()
        out = {"ok": True, "watch": wid, "feeds": report, "new": len(new_items), "baseline_run": first and w.get("baseline", True)}
        if alert:
            out["alert"] = alert["id"]
            if w.get("pin", True) and self.pin:
                out["pinned"] = self._pin(w, new_items)
            if w.get("deliver") == "inject" and self.deliver:
                try:
                    if self.deliver(w, format_alert(alert)):
                        with self.lock:
                            alert["delivered"] = "inject"
                            self._save()
                        out["delivered"] = "inject"
                except Exception:
                    pass
            out.setdefault("delivered", "queued")
        return out

    def _pin(self, w: dict, items: list[dict]) -> dict:
        anns = [{"type": "pin", "latitude": i["lat"], "longitude": i["lon"], "label": i["title"][:60],
                 "color": COLORS.get(i["feed"], "primary"), "title": i["title"],
                 "description_md": (f"**Watch:** {w['name']}\n\n" + (i.get("description_md") or ""))[:4000],
                 "links": i.get("links") or [], "tags": {**(i.get("tags") or {"category": i["feed"]}), "watch": w["name"][:80]}}
                for i in items[:MAX_PINS_PER_RUN]]
        try:
            res = self.pin(w.get("session_id"), anns)
            return {"ok": bool(isinstance(res, dict) and res.get("ok")), "count": len(anns),
                    **({"error": res.get("error") or res.get("code")} if isinstance(res, dict) and not res.get("ok") else {})}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}"}

    def due(self) -> list[str]:
        now = self.clock()
        with self.lock:
            return [w["id"] for w in self.data["watches"].values() if w.get("active") and (w.get("next_run") or 0) <= now]

    def history(self, session_id: str | None = None) -> list[dict]:
        """Stored alerts, including ones already delivered. Does not mark them delivered."""
        with self.lock:
            out = []
            for alert in self.data.get("alerts") or []:
                if session_id and alert.get("session_id") and str(alert["session_id"]) != str(session_id):
                    continue
                out.append(json.loads(json.dumps(alert)))
            return out

    def drain_alerts(self, session_id: str | None = None) -> list[dict]:
        """Undelivered alerts for this chat (plus alerts of watches made outside a chat); marks them delivered."""
        with self.lock:
            out = [a for a in self.data["alerts"] if not a.get("delivered")
                   and (not a.get("session_id") or not session_id or a["session_id"] == str(session_id))]
            for a in out:
                a["delivered"] = "turn"
            if out:
                self._save()
            return out

    # -- loop ---------------------------------------------------------------------------------
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def ensure_running(self) -> bool:
        if os.environ.get("GODSEYE_WATCH_LOOP", "1") == "0":
            return False
        with self.lock:
            if not any(w.get("active") for w in self.data["watches"].values()) or self.running():
                return self.running()
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="godseye-watch", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_S if self.data["watches"] else 0):
            with self.lock:
                if not any(w.get("active") for w in self.data["watches"].values()):
                    self._thread = None
                    return  # idle: nothing active, no thread
            for wid in self.due():
                try:
                    self.check(wid)
                except Exception:
                    pass


def format_alert(a: dict) -> str:
    feeds = {}
    for i in a["items"]:
        feeds.setdefault(i["feed"], []).append(i)
    lines = [f"[EOH watch alert] {a['name']}: {a['count']} new item(s)."]
    for f, its in feeds.items():
        lines.append(f"- {f}: " + "; ".join(f"{i['title']} ({i['lat']:.3f}, {i['lon']:.3f})" for i in its[:8])
                     + (f" (+{len(its) - 8} more)" if len(its) > 8 else ""))
    return "\n".join(lines)


def alerts_context(alerts: list[dict]) -> str:
    if not alerts:
        return ""
    body = "\n".join(format_alert(a) for a in alerts[:10])
    return ("Eye Of Hermes area-watch alerts arrived since the last turn. Tell the user about them briefly "
            "(they were also pinned on the EOH pane):\n" + body)[:6000]


_store: WatchStore | None = None


def store() -> WatchStore:
    global _store
    if _store is None:
        _store = WatchStore(default_path(), pin=_default_pin)
    return _store


def _default_pin(session_id, annotations) -> dict:
    from . import composer
    return composer.live_call("annotate_map", {"annotations": annotations}, session_id)
