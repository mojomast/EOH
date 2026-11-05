"""Desktop preview swap helper.

``desktop_preview`` is a Hermes *core* tool (``desktop_ui`` toolset). This plugin
cannot change its schema or make it close the previous tab on open. Tours and
brief flows that want a single preview tab should call
``godseye_preview_swap`` (or :func:`swap`) which tracks the last URL this
plugin opened and closes it before asking for a new one.

When the desktop emitter is available the helper emits ``preview.close`` /
``preview.open`` itself. Otherwise it returns structured close+open steps the
agent can run through ``desktop_preview``.
"""
from __future__ import annotations

import json
import threading
from typing import Callable

_lock = threading.Lock()
_last_url: str | None = None

LIMITATION = (
    "desktop_preview is a Hermes core tool; this plugin cannot add a native "
    "'swap' action to it. godseye_preview_swap tracks the last URL this plugin "
    "opened and closes it before opening the next one."
)


def last_url() -> str | None:
    with _lock:
        return _last_url


def remember(url: str | None) -> None:
    global _last_url
    with _lock:
        _last_url = (url or "").strip() or None


def clear() -> str | None:
    global _last_url
    with _lock:
        prev, _last_url = _last_url, None
        return prev


def _try_desktop(event: str, payload: dict) -> dict | None:
    """Emit via Hermes desktop_ui when running inside a desktop session."""
    try:
        from tools import desktop_ui  # type: ignore
    except Exception:
        return None
    if not getattr(desktop_ui, "available", lambda: False)():
        return None
    try:
        ok = desktop_ui.emit(event, payload)
        return {"ok": bool(ok), "event": event, "payload": {k: payload[k] for k in payload if k != "url" or True}}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def swap(url: str, label: str = "", closer: Callable | None = None,
         opener: Callable | None = None) -> dict:
    """Close the previous tracked preview URL (if any), then open ``url``.

    ``closer(url)`` / ``opener(url, label)`` override the desktop emit path
    (used by tests). Values are never logged beyond the URL itself.
    """
    target = (url or "").strip()
    if not target:
        return {"ok": False, "error": "url is required", "limitation": LIMITATION}
    prev = last_url()
    closed = None
    if prev and prev != target:
        if closer:
            closed = closer(prev)
        else:
            closed = _try_desktop("preview.close", {"url": prev})
            if closed is None:
                closed = {"ok": False, "deferred": True,
                          "call": {"tool": "desktop_preview", "action": "close", "url": prev}}
    opened = None
    if opener:
        opened = opener(target, label or "")
    else:
        payload = {"url": target, **({"label": label} if label else {})}
        opened = _try_desktop("preview.open", payload)
        if opened is None:
            opened = {"ok": False, "deferred": True,
                      "call": {"tool": "desktop_preview", "action": "open", "url": target,
                               **({"label": label} if label else {})}}
    remember(target)
    out = {"ok": True, "url": target, "closed": prev, "close": closed, "open": opened,
           "limitation": LIMITATION}
    if label:
        out["label"] = label
    # Agent-facing: when desktop emit isn't wired, spell out the two core-tool calls.
    if (isinstance(closed, dict) and closed.get("deferred")) or (isinstance(opened, dict) and opened.get("deferred")):
        steps = []
        if isinstance(closed, dict) and closed.get("call"):
            steps.append(closed["call"])
        if isinstance(opened, dict) and opened.get("call"):
            steps.append(opened["call"])
        out["desktop_preview_steps"] = steps
        out["ok"] = True  # tracking succeeded; agent still needs to run the steps
        out["note"] = ("desktop_preview is not callable from this plugin process; run the "
                       "desktop_preview_steps (close previous, then open) yourself.")
    return out


def close_tracked(closer: Callable | None = None) -> dict:
    prev = clear()
    if not prev:
        return {"ok": True, "closed": None, "limitation": LIMITATION}
    if closer:
        res = closer(prev)
    else:
        res = _try_desktop("preview.close", {"url": prev})
        if res is None:
            res = {"ok": False, "deferred": True,
                   "call": {"tool": "desktop_preview", "action": "close", "url": prev}}
    return {"ok": True, "closed": prev, "close": res, "limitation": LIMITATION}


def format_result(out: dict) -> str:
    return json.dumps(out, ensure_ascii=False)
