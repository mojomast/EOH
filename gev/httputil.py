"""Shared HTTP request-body limits for the loopback relays."""
from __future__ import annotations


class BadBody(ValueError):
    """Invalid or oversized Content-Length."""


def parse_content_length(value, max_body: int | None = None) -> int:
    """Return a non-negative body size in bytes.

    Rejects negatives, signs, decimals and non-digits so a caller never
    passes ``-1`` to ``read()`` (which means "read until EOF").
    """
    raw = str(value if value is not None else "").strip()
    if not raw.isdigit():
        raise BadBody("invalid content length")
    n = int(raw)
    if max_body is not None and n > max_body:
        raise BadBody("too large")
    return n
