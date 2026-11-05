"""Prepare God's Eye View's MCP Apps globe panel for the Hermes pane's sandboxed frame.

The scripts added here run only inside the opaque-origin SandboxedFrame (never in the Hermes
renderer): a <base> for the data: document, the opt-in GEV_EMBED_FULL_UI flag, a Cesium worker
shim (gev/panel_worker_shim.js), the grounded pin layer + info drawer (gev/panel_pins.js) and the live-globe agent (gev/panel_live_agent.js).
"""
from __future__ import annotations

import re
from pathlib import Path

_HERE = Path(__file__).parent
BASE_TAG = '<base href="https://app.gods-eye-view.invalid/">'


def _script(body: str) -> str:
    return "<" + "script>" + body + "</" + "script>"


def prepare(html: str, full_ui: bool = True) -> str:
    head = BASE_TAG
    if full_ui:
        head += _script("window.GEV_EMBED_FULL_UI = true")
    head += _script((_HERE / "panel_worker_shim.js").read_text())
    head += _script((_HERE / "panel_pins.js").read_text())
    head += _script((_HERE / "panel_live_agent.js").read_text())
    m = re.search(r"<head[^>]*>", html, re.I)
    return html[: m.end()] + head + html[m.end():] if m else head + html
