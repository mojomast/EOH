"""Public-only HTTP for URLs from untrusted pin links and OSM tags.

Resolve and vet every address, pin the connection to that result (no second
DNS lookup), and repeat for each redirect. No ambient proxy or URL credentials.

HTTP to a public address is the documented research exception for OSM website
tags and similar pin links. Loopback is allowed only when the caller opts in
and the host is exactly 127.0.0.1, localhost, or ::1 (local Overpass /
Nominatim during development). Metadata, CGNAT, and other non-global
addresses stay rejected even with that flag.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class UnsafeURL(ValueError):
    pass


def header_value(value: str, limit: int = 256) -> str:
    """Strip CR/LF and other controls from a header value."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:limit]


def _clean_headers(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key)
        if not name or any(c in name for c in ":\r\n") or any(ord(c) < 32 for c in name):
            continue
        out[name] = header_value(value)
    return out


def loopback_host(host: str | None) -> bool:
    return (host or "").strip().lower().rstrip(".") in LOOPBACK_HOSTS


def resolve_public(url: str, *, allow_loopback: bool = False):
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise UnsafeURL("Only public HTTP(S) URLs without credentials are allowed.")
        if any(c.isspace() or ord(c) < 32 for c in url) or "%" in parts.hostname:
            raise UnsafeURL("Invalid URL host.")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        local = allow_loopback and loopback_host(parts.hostname)
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise UnsafeURL("Host has no public addresses.")
        for family, kind, proto, canon, addr in addresses:
            ip = ipaddress.ip_address(addr[0])
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if local:
                if not ip.is_loopback:
                    raise UnsafeURL("Non-public destination refused.")
                continue
            if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                raise UnsafeURL("Non-public destination refused.")
        return parts, addresses
    except (ValueError, OSError) as exc:
        raise UnsafeURL("URL must resolve exclusively to public addresses.") from exc


def _connection(parts, addresses, timeout):
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=timeout)

    def connect_vetted(_address, timeout=timeout, source_address=None):
        # HTTPConnection invokes this instead of socket.create_connection; HTTPS
        # still uses the original hostname for certificate verification and SNI.
        last = None
        for family, kind, proto, canon, addr in addresses:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(timeout)
                sock.connect(addr)
                return sock
            except OSError as exc:
                last = exc
                sock.close()
        raise last or OSError("No public destination available")

    conn._create_connection = connect_vetted
    return conn


def request(url: str, *, data: bytes | None = None, timeout=12.0, headers=None,
            limit=2_000_000, max_redirects=4, allow_loopback: bool = False):
    """GET or POST with DNS pinning and per-hop address checks.

    POST and other bodies do not follow redirects, so a credential-bearing
    request cannot be forwarded to a different host.
    """
    headers = _clean_headers(headers)
    method = "POST" if data is not None else "GET"
    if data is not None and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    hops = 0 if data is not None else max_redirects
    for hop in range(hops + 1):
        parts, addresses = resolve_public(url, allow_loopback=allow_loopback)
        conn = _connection(parts, addresses, timeout)
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            if data is None and response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or hop == hops:
                    raise UnsafeURL("Redirect limit or missing destination.")
                url = urljoin(url, location)
                continue  # next iteration validates and pins the next destination
            return response.status, response.read(limit).decode("utf-8", "replace")
        finally:
            conn.close()
    raise UnsafeURL("Redirect limit exceeded.")


def get(url: str, *, timeout=12.0, headers=None, limit=2_000_000, max_redirects=4,
        allow_loopback: bool = False):
    return request(url, timeout=timeout, headers=headers, limit=limit,
                   max_redirects=max_redirects, allow_loopback=allow_loopback)
