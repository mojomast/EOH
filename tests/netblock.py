"""Block public network during tests and replay hand-written cassettes.

Pytest installs this by default (see tests/conftest.py). Loopback stays open so the
relay and bridge tests can use a local HTTP server. Every other host raises
NetworkBlocked unless the test plays a cassette. Cassettes are JSON files under
tests/fixtures/cassettes and must not contain keys, tokens, or private addresses.
"""
from __future__ import annotations

import contextvars
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

CASSETTE_DIR = Path(__file__).resolve().parent / "fixtures" / "cassettes"
REQUIRED = (
    "osrm_route",
    "valhalla_isochrone",
    "overpass_poi",
    "nominatim",
    "usgs_quakes",
    "firms_csv",
    "opensky_states",
    "wayback_config",
)

_active: contextvars.ContextVar[frozenset] = contextvars.ContextVar("godseye_cassettes", default=frozenset())
_REAL = None
_REAL_CONNECT = None
_loaded: dict[str, list] = {}


class NetworkBlocked(OSError):
    """Raised instead of contacting a public host."""


class _Body:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.code = status
        self._body = body
        self._off = 0

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._body) - self._off
        chunk = self._body[self._off:self._off + n]
        self._off += len(chunk)
        return chunk

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def cassette_names() -> list[str]:
    return sorted(p.stem for p in CASSETTE_DIR.glob("*.json"))


def _load(name: str) -> list:
    if name not in _loaded:
        data = json.loads((CASSETTE_DIR / f"{name}.json").read_text())
        _loaded[name] = list(data.get("interactions") or [])
    return _loaded[name]


def _host_allowed(host: str) -> bool:
    host = str(host or "").strip("[]").rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_url(url: str) -> bool:
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return bool(host) and _host_allowed(host)


def _safe(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    segs = []
    for seg in parts.path.split("/"):
        if re.fullmatch(r"[A-Fa-f0-9]{16,}", seg):
            seg = "REDACTED"
        elif (len(seg) >= 12 and re.search(r"\d", seg) and re.fullmatch(r"[A-Za-z0-9_\-]+", seg)
              and not seg.upper().startswith(("VIIRS", "MODIS"))):
            seg = "REDACTED"
        segs.append(seg)
    return f"{parts.scheme}://{parts.hostname}{'/'.join(segs)}"


def _match(req: urllib.request.Request, inter: dict) -> bool:
    want = inter.get("request") or {}
    if want.get("method") and want["method"].upper() != req.get_method().upper():
        return False
    parts = urllib.parse.urlsplit(req.full_url)
    host = (parts.hostname or "").lower()
    hosts = want.get("host")
    if isinstance(hosts, str):
        hosts = [hosts]
    if hosts and host not in [h.lower() for h in hosts]:
        return False
    needle = want.get("path_contains")
    if needle and needle not in (parts.path or ""):
        return False
    return True


def _response(spec: dict) -> _Body:
    status = int(spec.get("status") or 200)
    if "json" in spec:
        body = json.dumps(spec["json"]).encode()
    else:
        body = str(spec.get("text") or "").encode()
    return _Body(status, body)


def _lookup(req: urllib.request.Request):
    for name in _active.get():
        for inter in _load(name):
            if _match(req, inter):
                return _response(inter.get("response") or {})
    return None


def guarded(*args, **kwargs):
    if not args:
        raise TypeError("urlopen requires a url")
    url = args[0]
    target = url.full_url if isinstance(url, urllib.request.Request) else str(url)
    if _is_local_url(target):
        return _REAL(*args, **kwargs)
    req = url if isinstance(url, urllib.request.Request) else urllib.request.Request(
        target, data=args[1] if len(args) > 1 else kwargs.get("data"))
    hit = _lookup(req)
    if hit is not None:
        return hit
    raise NetworkBlocked(
        f"public network is disabled in tests ({_safe(target)}). Play a cassette or use loopback.")


def _guard_connect(address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, bytes):
        host = host.decode()
    if not _host_allowed(str(host)):
        raise NetworkBlocked(f"public network is disabled in tests (connection to {host})")
    return _REAL_CONNECT(address, *args, **kwargs)


def install(monkeypatch) -> None:
    """Replace urlopen and socket connections for the current test."""
    global _REAL, _REAL_CONNECT
    if _REAL is None:
        _REAL = urllib.request.urlopen
        _REAL_CONNECT = socket.create_connection
    monkeypatch.setattr(urllib.request, "urlopen", guarded)
    monkeypatch.setattr(socket, "create_connection", _guard_connect)


def deactivate() -> None:
    _active.set(frozenset())


@contextmanager
def playing(*names: str):
    """Serve the named cassettes for public urlopen calls inside the block."""
    missing = [n for n in names if not (CASSETTE_DIR / f"{n}.json").is_file()]
    if missing:
        raise FileNotFoundError(f"missing cassettes: {', '.join(missing)}")
    token = _active.set(frozenset(names))
    try:
        yield
    finally:
        _active.reset(token)
