"""Query public SDR receivers through the God's Eye View directory API.

The GEV MCP catalog has no SDR tool (find_radio_stations is internet radio and
is MCP-off). The running app exposes the same ReceiverBook / KiwiSDR / Airspy /
UberSDR directory the sdr-receivers layer uses at ``GET /api/sdr/receivers``.
This op ranks those public stations by distance and never starts audio.
"""
from __future__ import annotations

import json
import math
from urllib.parse import urlencode, urlsplit, quote
from urllib.request import ProxyHandler, Request, build_opener

from . import config
from .ext_ops import ExtOp

PLATFORMS = {
    "kiwi": "kiwi",
    "kiwisdr": "kiwi",
    "openwebrx": "openwebrx",
    "owrx": "openwebrx",
    "websdr": "websdr",
    "spyserver": "spyserver",
    "airspy": "spyserver",
    "ubersdr": "ubersdr",
    "uber": "ubersdr",
}
BANDS_HZ = {
    "hf": (3e6, 30e6),
    "vhf": (30e6, 300e6),
    "uhf": (300e6, 3e9),
}
BAND_ALIASES = {
    "hf": "hf", "vhf": "vhf", "uhf": "uhf",
    "160m": "hf", "80m": "hf", "40m": "hf", "30m": "hf", "20m": "hf",
    "17m": "hf", "15m": "hf", "12m": "hf", "10m": "hf", "11m": "hf", "cb": "hf",
    "6m": "vhf", "2m": "vhf", "air": "vhf", "airband": "vhf",
    "70cm": "uhf", "23cm": "uhf", "33cm": "uhf",
}
DEFAULT_RADIUS_KM = 250.0
MAX_RADIUS_KM = 2000.0
DEFAULT_LIMIT = 8
MAX_LIMIT = 25
FETCH_LIMIT = 4000
FETCH_TIMEOUT_S = 25.0
FETCH_MAX_BYTES = 8 * 1024 * 1024
SDR_MIN_REV = "b361a25"
FIX_UPDATE = (
    "Update the God's Eye View checkout to mojomast/gods-eye-view main at or "
    f"after {SDR_MIN_REV} (pull request #5), restart the app, then retry "
    "godseye_query op=sdr_receivers."
)
FIX_START = (
    "Start the God's Eye View app (godseye_admin op=app_start, or npm run dev "
    "at GODSEYE_API_BASE, default http://localhost:4173) on a checkout at or "
    f"after {SDR_MIN_REV}, then retry godseye_query op=sdr_receivers. "
    "godseye_admin op=doctor checks that the app is reachable."
)
HERE = {
    "me", "here", "nearby", "near me", "around here", "around me",
    "my location", "this", "this area", "the area", "this view", "current view",
}


def _fail(error: str, message: str, fix: str, **extra) -> str:
    body = {"ok": False, "error": error, "code": error, "message": message, "fix": fix}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def canonical_platform(value) -> str | None:
    if value is None or value == "":
        return None
    key = str(value).strip().lower().replace(" ", "")
    return PLATFORMS.get(key)


def canonical_band(value) -> str | None:
    if value is None or value == "":
        return None
    key = str(value).strip().lower().replace(" ", "")
    return BAND_ALIASES.get(key)


def covers_hz(record: dict, low: float, high: float) -> bool:
    rng = record.get("bandsHz") or record.get("bands_hz")
    if not (isinstance(rng, (list, tuple)) and len(rng) >= 2):
        return False
    try:
        start, end = float(rng[0]), float(rng[1])
    except (TypeError, ValueError):
        return False
    return math.isfinite(start) and math.isfinite(end) and end >= low and start <= high


def band_labels(record: dict) -> list[str]:
    return [name for name, (low, high) in BANDS_HZ.items() if covers_hz(record, low, high)]


def available(record: dict) -> bool:
    if record.get("online") is False:
        return False
    users, cap = _num(record.get("users")), _num(record.get("usersMax") or record.get("users_max"))
    if users is not None and cap is not None and users >= cap:
        return False
    return True


def matches(record: dict, filt: dict) -> bool:
    platform = filt.get("platform")
    if platform and record.get("platform") != platform:
        return False
    band = filt.get("band")
    if band:
        low, high = BANDS_HZ[band]
        if not covers_hz(record, low, high):
            return False
    freq = filt.get("frequency_hz")
    if freq is not None and not covers_hz(record, freq, freq):
        return False
    if filt.get("available") and not available(record):
        return False
    if filt.get("playable") and not record.get("playable"):
        return False
    if filt.get("waterfall") and not (record.get("waterfall") or record.get("playable")):
        return False
    query = filt.get("query")
    if query:
        haystack = " ".join(
            str(record.get(key) or "")
            for key in ("name", "site", "owner", "hardware", "platform", "id")
        ).lower()
        if query not in haystack:
            return False
    return True


def mode_for_frequency(freq_hz: float | None, band: str | None) -> str:
    if freq_hz is not None and freq_hz < 30e6:
        return "lsb"
    if band == "hf":
        return "lsb"
    return "nbfm"


def deep_link(record: dict, freq_mhz: float | None = None, band: str | None = None) -> str:
    ident = quote(str(record.get("id") or ""), safe="")
    parts = [f"sdr={ident}", "wf=1"]
    if freq_mhz is not None:
        parts.append(f"f={freq_mhz:g}")
        parts.append(f"m={mode_for_frequency(freq_mhz * 1e6, band)}")
    return "?" + "&".join(parts)


def summarize(record: dict, origin=None, freq_mhz=None, band=None) -> dict:
    lat, lon = _num(record.get("lat")), _num(record.get("lon"))
    row = {
        "id": record.get("id"),
        "name": record.get("name"),
        "platform": record.get("platform"),
        "lat": lat,
        "lon": lon,
        "playable": bool(record.get("playable")),
        "waterfall": bool(record.get("waterfall") or record.get("playable")),
        "bands": band_labels(record),
        "url": record.get("url"),
        "embed": record.get("embed"),
        "online": record.get("online"),
        "deep_link": deep_link(record, freq_mhz, band),
    }
    users = _num(record.get("users"))
    cap = _num(record.get("usersMax") or record.get("users_max"))
    if users is not None:
        row["users"] = int(users)
    if cap is not None:
        row["users_max"] = int(cap)
    if origin and lat is not None and lon is not None:
        row["km"] = round(haversine_km(origin[0], origin[1], lat, lon), 1)
    return {key: value for key, value in row.items() if value is not None}


def filter_records(records: list, filt: dict, origin=None, radius_km=None, limit=DEFAULT_LIMIT) -> dict:
    matched = [row for row in records if isinstance(row, dict) and matches(row, filt)]
    if origin:
        usable = []
        for row in matched:
            lat, lon = _num(row.get("lat")), _num(row.get("lon"))
            if lat is None or lon is None:
                continue
            distance = haversine_km(origin[0], origin[1], lat, lon)
            if radius_km is not None and distance > radius_km:
                continue
            usable.append((distance, row))
        usable.sort(key=lambda item: (item[0], item[1].get("name") or ""))
        matched = [row for _, row in usable]
    counts: dict[str, int] = {}
    for row in matched:
        plat = str(row.get("platform") or "other")
        counts[plat] = counts.get(plat, 0) + 1
    clipped = matched[:limit]
    freq_mhz = (filt.get("frequency_hz") / 1e6) if filt.get("frequency_hz") else None
    return {
        "total": len(matched),
        "truncated": len(matched) > len(clipped),
        "counts": counts,
        "receivers": [summarize(row, origin, freq_mhz, filt.get("band")) for row in clipped],
    }


def _local_get(url: str, timeout: float = FETCH_TIMEOUT_S, limit: int = FETCH_MAX_BYTES):
    """GET a URL on the configured God's Eye View API base. No user-supplied URL."""
    import urllib.error
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("api_base must be a credential-free HTTP URL")
    opener = build_opener(ProxyHandler({}))
    request = Request(url, method="GET", headers={"Accept": "application/json", "User-Agent": "EyeOfHermes/sdr-query"})
    try:
        with opener.open(request, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status in {301, 302, 303, 307, 308}:
                raise ValueError("redirects are not followed")
            return status, resp.read(limit)
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(limit)
        except Exception:
            raw = b""
        return exc.code, raw


def fetch_directory(filt: dict, fetch=None, api_base: str | None = None) -> dict:
    base = (api_base if api_base is not None else config.api_base()).rstrip("/")
    params = {"limit": str(FETCH_LIMIT), "offset": "0"}
    if filt.get("platform"):
        params["platform"] = filt["platform"]
    if filt.get("band"):
        params["band"] = filt["band"]
    if filt.get("query"):
        params["q"] = filt["query"]
    if filt.get("available"):
        params["available"] = "1"
    if filt.get("playable"):
        params["playable"] = "1"
    if filt.get("waterfall"):
        params["waterfall"] = "1"
    url = f"{base}/api/sdr/receivers?{urlencode(params)}"
    getter = fetch or _local_get
    try:
        status, raw = getter(url)
    except Exception as exc:  # connection refused, timeout, bad api_base
        raise DirectoryError("sdr_unavailable", f"The God's Eye View SDR directory did not answer: {exc}.", FIX_START) from exc
    if status == 404:
        raise DirectoryError(
            "sdr_unsupported",
            "This God's Eye View app has no /api/sdr/receivers endpoint.",
            FIX_UPDATE,
        )
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    if status in (502, 503) or status == 200 and "sdr_directory_unavailable" in text:
        try:
            payload = json.loads(text) if text else {}
        except ValueError:
            payload = {}
        message = (payload.get("message") if isinstance(payload, dict) else None) or "The public SDR directory is unavailable."
        raise DirectoryError("sdr_unavailable", str(message)[:400], FIX_START)
    if not isinstance(status, int) or not (200 <= status < 300):
        raise DirectoryError(
            "sdr_unavailable",
            f"The SDR directory returned HTTP {status}.",
            FIX_START,
        )
    try:
        payload = json.loads(text)
    except ValueError:
        raise DirectoryError("sdr_unavailable", "The SDR directory returned invalid JSON.", FIX_START)
    if not isinstance(payload, dict):
        raise DirectoryError("sdr_unavailable", "The SDR directory returned an unexpected body.", FIX_START)
    records = payload.get("receivers")
    if not isinstance(records, list):
        records = []
    return payload, records


class DirectoryError(Exception):
    def __init__(self, code: str, message: str, fix: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix


def _is_here(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in HERE


def _origin(args: dict, session_id=None):
    """Resolve a search centre from here, lat/lon, or place. Returns (point, radius, hint, error)."""
    here = bool(args.get("here")) or _is_here(args.get("place")) or _is_here(args.get("query"))
    lat, lon = _num(args.get("lat")), _num(args.get("lon"))
    radius = _num(args.get("radius_km"))
    if radius is not None:
        radius = max(0.1, min(MAX_RADIUS_KM, radius))
    place = args.get("place")
    if _is_here(place):
        place = None
    if here and lat is None:
        from .router import camera
        point, err = camera(session_id)
        if err:
            return None, None, None, err
        origin = (point["lat"], point["lon"])
        if radius is None:
            rng = point.get("rangeM")
            radius = max(50.0, min(MAX_RADIUS_KM, (rng / 1000.0) * 4)) if isinstance(rng, (int, float)) and rng > 0 else DEFAULT_RADIUS_KM
        return origin, radius, {"source": "pane", "lat": origin[0], "lon": origin[1]}, None
    if lat is not None and lon is not None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, None, None, {
                "ok": False, "error": "bad_args", "code": "bad_args",
                "message": "lat and lon are out of range.",
                "fix": "Pass lat between -90 and 90 and lon between -180 and 180.",
            }
        return (lat, lon), radius if radius is not None else DEFAULT_RADIUS_KM, {
            "source": "args", "lat": lat, "lon": lon,
        }, None
    if isinstance(place, str) and place.strip():
        from . import poi
        hit = poi.geocode(place.strip())
        if not isinstance(hit, dict) or not hit.get("ok"):
            fail = hit if isinstance(hit, dict) else {}
            return None, None, None, {
                "ok": False,
                "error": fail.get("error") or "geocoder_unavailable",
                "code": fail.get("error") or "geocoder_unavailable",
                "message": fail.get("message") or "The place could not be geocoded.",
                "fix": fail.get("fix") or "Pass a place with a state or country, or use lat/lon or here=true.",
            }
        origin = (hit["lat"], hit["lon"])
        return origin, radius if radius is not None else DEFAULT_RADIUS_KM, {
            "source": "place", "place": hit.get("name") or place, "lat": origin[0], "lon": origin[1],
        }, None
    return None, radius, None, None


def _filters(args: dict) -> tuple[dict | None, str | None]:
    platform_raw = args.get("platform")
    platform = canonical_platform(platform_raw) if platform_raw not in (None, "") else None
    if platform_raw not in (None, "") and platform is None:
        return None, _fail(
            "bad_args", f"Unknown platform {platform_raw!r}.",
            "Use platform kiwi, openwebrx, websdr, spyserver, or ubersdr.",
        )
    band_raw = args.get("band")
    band = canonical_band(band_raw) if band_raw not in (None, "") else None
    if band_raw not in (None, "") and band is None:
        return None, _fail(
            "bad_args", f"Unknown band {band_raw!r}.",
            "Use band hf, vhf, or uhf (2m, 70cm, 20m, and airband are accepted).",
        )
    freq = _num(args.get("frequency_mhz"))
    if freq is not None and not (0.01 <= freq <= 30000):
        return None, _fail(
            "bad_args", "frequency_mhz is out of range.",
            "Pass frequency_mhz as a frequency in megahertz, for example 7.14 or 145.5.",
        )
    query = args.get("query")
    if _is_here(query):
        query = None
    elif isinstance(query, str):
        query = query.strip().lower() or None
    else:
        query = None
    waterfall = args.get("waterfall")
    playable = args.get("playable")
    all_catalog = args.get("all") is True
    if waterfall is None and playable is None and not all_catalog:
        waterfall = True
    return {
        "platform": platform,
        "band": band,
        "frequency_hz": (freq * 1e6) if freq is not None else None,
        "query": query,
        "available": args.get("available") is True,
        "playable": playable is True,
        "waterfall": waterfall is True,
    }, None


def _pin(rows: list[dict], args: dict, session_id=None):
    from . import composer
    annotations = []
    for row in rows:
        if row.get("lat") is None or row.get("lon") is None:
            continue
        bands = ", ".join(row.get("bands") or []) or "bands unknown"
        play = "in-pane audio+waterfall" if row.get("playable") else "link out (OPEN)"
        description = f"{row.get('platform') or 'receiver'} · {bands} · {play}"
        if row.get("km") is not None:
            description += f" · {row['km']} km"
        item = {
            "type": "pin",
            "id": row.get("id"),
            "label": row.get("name") or row.get("id"),
            "latitude": row["lat"],
            "longitude": row["lon"],
            "description_md": description,
            "tags": {"category": "sdr-receivers", "platform": row.get("platform") or ""},
        }
        if row.get("url"):
            item["links"] = [{"url": row["url"], "label": "Receiver page"}]
        annotations.append(item)
    if not annotations:
        return {"ok": False, "error": "no_pins", "message": "No receivers to pin."}
    intent = args.get("intent") or args.get("context") or "public SDR receivers"
    return composer.live_call(
        "annotate_map",
        {"annotations": annotations, "intent": intent, "persist": True},
        session_id,
    )


def sdr_receivers(args: dict, **kw) -> str:
    args = dict(args or {})
    filt, err = _filters(args)
    if err:
        return err
    origin, radius, hint, loc_err = _origin(args, kw.get("session_id"))
    if loc_err:
        loc_err.setdefault("ok", False)
        loc_err.setdefault("error", loc_err.get("code") or "no_focus")
        loc_err.setdefault("code", loc_err["error"] if isinstance(loc_err.get("error"), str) else "no_focus")
        return json.dumps(loc_err, ensure_ascii=False)
    limit = _num(args.get("limit"))
    limit = DEFAULT_LIMIT if limit is None else max(1, min(MAX_LIMIT, int(limit)))
    try:
        payload, records = fetch_directory(filt, fetch=kw.get("fetch"), api_base=kw.get("api_base"))
    except DirectoryError as exc:
        return _fail(exc.code, exc.message, exc.fix)
    # Frequency is not a GEV query param; apply it (and radius) locally.
    result = filter_records(records, filt, origin, radius, limit)
    body = {
        "ok": True,
        "op": "sdr_receivers",
        "total": result["total"],
        "truncated": result["truncated"],
        "counts": result["counts"],
        "waterfall_only": bool(filt.get("waterfall")),
        "receivers": result["receivers"],
        "listen": "A pin click or an explicit request starts audio. Do not start a stream from this list alone.",
    }
    if hint:
        body["origin"] = {**hint, "radius_km": radius}
    if isinstance(payload, dict) and payload.get("stale"):
        body["stale"] = True
    if isinstance(payload, dict) and payload.get("failedSources"):
        body["failed_sources"] = payload["failedSources"]
    if origin is None and not result["receivers"]:
        body["message"] = "Pass place, here=true, or lat and lon to rank receivers by distance."
        body["fix"] = "Retry godseye_query op=sdr_receivers with place, here=true, or lat and lon."
    elif origin is not None and not result["receivers"]:
        body["message"] = "No public SDR receivers matched in that area."
        body["fix"] = (
            "Widen radius_km, pass waterfall=false for the full directory "
            "(including HTTPS and SpyServer), or drop band/platform filters."
        )
    if args.get("pin") and result["receivers"]:
        body["pinned"] = _pin(result["receivers"], args, kw.get("session_id"))
    return json.dumps(body, ensure_ascii=False)


EXT_OPS = [
    ExtOp(
        tool="godseye_query",
        op="sdr_receivers",
        handler=sdr_receivers,
        props={
            "here": {"type": "boolean", "description": "Use the open pane camera as the search centre."},
            "place": {"type": "string", "description": "Place name to search around. 'here' uses the pane camera."},
            "lat": {"type": "number", "minimum": -90, "maximum": 90},
            "lon": {"type": "number", "minimum": -180, "maximum": 180},
            "radius_km": {"type": "number", "minimum": 0.1, "maximum": 2000,
                          "description": "Search radius in kilometres. Default 250."},
            "query": {"type": "string", "description": "Optional name or site text to match."},
            "platform": {
                "type": "string",
                "enum": ["kiwi", "kiwisdr", "openwebrx", "owrx", "websdr", "spyserver", "airspy", "ubersdr", "uber"],
                "description": "Receiver platform. kiwisdr means kiwi; airspy means spyserver.",
            },
            "band": {
                "type": "string",
                "enum": ["hf", "vhf", "uhf", "160m", "80m", "40m", "20m", "15m", "10m", "6m", "2m", "70cm", "airband", "cb"],
                "description": "Coverage filter. 2m and airband are vhf; 70cm is uhf; 20m is hf.",
            },
            "frequency_mhz": {"type": "number", "description": "Keep receivers whose advertised range covers this MHz."},
            "waterfall": {"type": "boolean", "description": "Default true: only audio+waterfall (KiwiSDR/OpenWebRX http) receivers."},
            "playable": {"type": "boolean", "description": "Only receivers that can play natively in the pane."},
            "available": {"type": "boolean", "description": "Drop offline or full receivers."},
            "all": {"type": "boolean", "description": "Search the full directory, including HTTPS and SpyServer/UberSDR."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "description": "Maximum rows to return. Default 8."},
            "pin": {"type": "boolean", "description": "Pin the returned receivers on the open pane. Does not start audio."},
            "intent": {"type": "string", "description": "User request used to fill pin drawers when pin=true."},
            "context": {"type": "string", "description": "Alias of intent."},
        },
        blurb="Nearest public SDR receivers to a point (here, place, or lat/lon). Filter by platform, band, or frequency_mhz. Defaults to audio+waterfall. Does not start audio.",
    ),
]
