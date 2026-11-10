"""Keyed-provider probes using Hermes-scoped credentials.

A probe makes one cheap call and returns ok, invalid, quota, unreachable, or
missing. Key values are never returned, logged, or copied into an error.
Users configure credentials through Hermes; the plugin cannot install keys.
"""
from __future__ import annotations

from urllib.parse import urlencode

from .composer import CLIENT_KEYS, SERVER_KEYS, scrub_text

TOMTOM_KEY = "TOMTOM_API_KEY"
ALLOWED_KEYS = tuple(dict.fromkeys((*CLIENT_KEYS.keys(), *SERVER_KEYS.keys(), TOMTOM_KEY)))
_SECRET_CHARS = 6

_PROVIDER_KEY = {
    "google": "GOOGLE_MAPS_API_KEY (or GOOGLE_API_KEY)",
    "mapillary": "MAPILLARY_CLIENT_TOKEN",
    "ais": "AISSTREAM_API_KEY",
    "firms": "FIRMS_MAP_KEY",
    "tomtom": "TOMTOM_API_KEY",
    "tomtom-tile": "TOMTOM_API_KEY",
}
_PROVIDER_FIX = {
    "ais": "AIS feed is down or timed out. Check AISSTREAM_API_KEY and that the app's AIS stream is connected, "
           "then retry godseye_admin op=doctor with probe=true once.",
    "firms": "FIRMS feed is down or timed out. Check FIRMS_MAP_KEY and retry godseye_admin op=doctor with probe=true once.",
    "tomtom": "TomTom is not answering. Check TOMTOM_API_KEY, then reload the pane and retry the probe once.",
    "tomtom-tile": "TomTom tiles are not answering. Check TOMTOM_API_KEY, then reload the pane and retry the probe once.",
    "google": "Google is not answering. Check GOOGLE_MAPS_API_KEY, then retry the probe once.",
    "mapillary": "Mapillary is not answering. Check MAPILLARY_CLIENT_TOKEN, then retry the probe once.",
}


def feed_fix(provider: str, status: str) -> str | None:
    """Next step for a probe row. Never contains key material or local paths."""
    provider = str(provider or "")
    status = str(status or "")
    if status in ("ok", ""):
        return None
    if status == "missing":
        name = _PROVIDER_KEY.get(provider, "the provider key")
        return f"Configure {name} through Hermes for the active profile. Never print the value."
    if status == "quota":
        return "The provider quota is exhausted. Wait for the quota window or use another configured provider, then retry once."
    if status == "invalid":
        return f"The provider refused the key. Check {_PROVIDER_KEY.get(provider, 'the provider key')} and retry once. Never print the value."
    if status in ("timeout", "unreachable"):
        return _PROVIDER_FIX.get(provider) or "The provider did not answer. Retry the probe once; if it still fails, check the key and network."
    return _PROVIDER_FIX.get(provider)


def _key(name: str, file_env: dict) -> str:
    from .security import secret
    return secret(name)


def classify_probe(status, body: str = "") -> str:
    text = scrub_text(body or "").lower()
    if isinstance(status, str) and status.lower() in ("timeout", "timed_out", "timedout"):
        return "timeout"
    if status is None:
        return "unreachable"
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "unreachable"
    if code == 429 or "over_query_limit" in text or "over_daily_limit" in text or "quota" in text:
        return "quota"
    if code in (401, 403) or "request_denied" in text or "invalid_key" in text or "invalid key" in text:
        return "invalid"
    if "status" in text and "request_denied" in text:
        return "invalid"
    if 200 <= code < 300:
        if "request_denied" in text:
            return "invalid"
        if "over_query_limit" in text or "over_daily_limit" in text:
            return "quota"
        return "ok"
    if code >= 500:
        return "unreachable"
    return "invalid"


def _get(url: str, headers: dict | None, fetch, timeout: float = 4) -> tuple[int | str | None, str]:
    try:
        got = fetch(url, headers or {}, timeout)
    except TimeoutError:
        return "timeout", ""
    except Exception:
        return None, ""
    if isinstance(got, tuple):
        code = got[0] if isinstance(got[0], int) else None
        raw = got[1] if len(got) > 1 else b""
        if isinstance(raw, bytes):
            raw = raw[:500].decode("utf-8", "replace")
        return code, scrub_text(str(raw or ""))[:500]
    if isinstance(got, int):
        return got, ""
    return None, ""


def _row(provider: str, status: str) -> dict:
    row = {"provider": provider, "status": status}
    fix = feed_fix(provider, status)
    if fix:
        row["fix"] = fix
    return row


def probe_providers(fetch=None, file_env: dict | None = None, api_base: str | None = None) -> list[dict]:
    """One cheap call per keyed provider. The result has no key material."""
    from . import config
    env = {}
    base = (api_base if api_base is not None else config.api_base()).rstrip("/")
    if fetch is None:
        fetch = _urllib_fetch
    google = _key("GOOGLE_MAPS_API_KEY", env) or _key("GOOGLE_API_KEY", env)
    mapillary = _key("MAPILLARY_CLIENT_TOKEN", env)
    rows = []
    if not google:
        rows.append(_row("google", "missing"))
    else:
        query = urlencode({"address": "Montreal", "key": google})
        code, body = _get("https://maps.googleapis.com/maps/api/geocode/json?" + query, None, fetch)
        rows.append(_row("google", classify_probe(code, body)))
    if not mapillary:
        rows.append(_row("mapillary", "missing"))
    else:
        query = urlencode({
            "fields": "id", "limit": "1",
            "bbox": "-73.58,45.50,-73.56,45.52",
        })
        code, body = _get(
            "https://graph.mapillary.com/images?" + query,
            {"Authorization": f"OAuth {mapillary}"},
            fetch,
        )
        rows.append(_row("mapillary", classify_probe(code, body)))
    for provider, key_name, path in (
        ("ais", "AISSTREAM_API_KEY", "/api/vessels?limit=1"),
        ("firms", "FIRMS_MAP_KEY", "/api/firms/status"),
        ("tomtom", TOMTOM_KEY, "/api/tomtom/status"),
    ):
        if not _key(key_name, env):
            rows.append(_row(provider, "missing"))
            continue
        code, body = _get(base + path, None, fetch)
        status = classify_probe(code, body)
        row = _row(provider, status)
        if provider == "tomtom":
            row["cache"] = "reload the pane"
        rows.append(row)
    if _key(TOMTOM_KEY, env):
        code, body = _get(base + "/api/tomtom/flow/0/0/0.png", None, fetch)
        rows.append({**_row("tomtom-tile", classify_probe(code, body)), "cache": "reload the pane"})
    return rows


def _urllib_fetch(url: str, headers: dict, timeout: float):
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    # Official provider hosts whose probes put a key in the URL or Authorization
    # header must use the public-address, redirect-pinning helper. The local
    # God's Eye View app (GODSEYE_API_BASE) stays on urllib so a tailnet or
    # LAN API base still works; redirects are disabled on that path.
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host in {"maps.googleapis.com", "graph.mapillary.com"}:
        from .public_http import UnsafeURL, request as public_request
        try:
            status, body = public_request(url, timeout=timeout, headers=headers, limit=400)
        except UnsafeURL:
            return None, b""
        except TimeoutError:
            return "timeout", b""
        except Exception:
            return None, b""
        return status, body.encode("utf-8", "replace")[:400]

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: A003
            return None

    request = urllib.request.Request(url, method="GET", headers=dict(headers or {}))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as resp:
            return getattr(resp, "status", 200), resp.read(400)
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(400)
        except Exception:
            raw = b""
        return exc.code, raw
    except TimeoutError:
        return "timeout", b""
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            return "timeout", b""
        return None, b""
    except Exception:
        return None, b""
