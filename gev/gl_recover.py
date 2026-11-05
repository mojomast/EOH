"""Automatic WebGL recovery policy and the notices the relay hands to the agent.

The pane destroys the Cesium viewer and creates a new one in the same container,
then replays ``SceneStore.restore_commands``. These notices use the same
``{code, message, fix}`` shape as ``gev.errors``. Env wins over the config file.
Both default to on.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ENV = "GODSEYE_GL_RECOVER"
# Keep in step with MAX_FAILURES / WINDOW_MS in gev/gl_recover.js.
MAX_FAILURES = 3
WINDOW_MS = 20_000

_OFF = {"0", "false", "off", "no"}
_LOST = {
    "recovering": (
        "The Eye Of Hermes (EOH) pane lost its WebGL context. The viewer is being destroyed and created again in the same pane, then the saved scene is restored.",
        "Wait for automatic recovery. Camera heading, pitch, and roll, plus pins, annotations, the drawer, filters, and layers come back from the saved scene. If a Click to reload the globe card appears, ask the user to click it once. Do not rebuild the panel and do not open a second pane.",
    ),
    "exhausted": (
        "The Eye Of Hermes (EOH) pane lost its WebGL context and automatic recovery stopped after repeated failures in a short window. The click-to-reload card is showing.",
        "Ask the user to click Click to reload the globe once. Do not rebuild the panel and do not open a second pane. If it fails again, close other 3D or map tabs and reopen this browser tab.",
    ),
    "manual": (
        "The Eye Of Hermes (EOH) pane lost its WebGL context. Automatic recovery is off, or the browser could not start 3D graphics. The click-to-reload card is showing.",
        "Ask the user to click Click to reload the globe once. Set GODSEYE_GL_RECOVER=1, or glRecover true in plugin-data/godseye/config.json, and reload the pane to turn automatic recovery back on. Do not open a second pane.",
    ),
}


def config_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    return home / "plugin-data" / "godseye" / "config.json"


def file_config() -> dict:
    """Only the recovery switch. The rest of the file is ignored and never logged."""
    path = config_path()
    try:
        if not path.is_file() or path.stat().st_size > 65536:
            return {}
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in ("glRecover", "gl_recover") if key in data}


def _specified(value) -> bool:
    return value is not None and str(value).strip() != ""


def _on(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _OFF


def enabled(env=None, config=None) -> bool:
    """On unless env or config turns it off. A set env wins over the config file."""
    if env is None:
        env = os.environ.get(ENV)
    if _specified(env):
        return _on(env)
    if config is None:
        config = file_config()
    if isinstance(config, dict):
        for key in ("glRecover", "gl_recover"):
            if key in config and _specified(config[key]):
                return _on(config[key])
    return True


def context_lost_notice(kind: str = "recovering") -> dict:
    """Structured failure the agent can act on. ``error`` stays the code string."""
    message, fix = _LOST.get(kind, _LOST["recovering"])
    return {"ok": False, "error": "CONTEXT_LOST", "code": "CONTEXT_LOST", "message": message, "fix": fix}


def fixture_commands() -> list[tuple[str, dict]]:
    """A saved scene used by the headless harness and the unit test.

    Built only through ``SceneStore.restore_commands``, so the harness restores
    the same command list the pane asks the relay for.
    """
    from .scene import SceneStore
    st = SceneStore(None)
    st.record("s", "fly_to_location", {"latitude": 35.6, "longitude": 139.7, "rangeM": 2500},
              {"ok": True, "result": {"camera": {
                  "latitude": 35.6, "longitude": 139.7, "heightM": 2500,
                  "targetLat": 35.6895, "targetLon": 139.6917, "rangeM": 2500,
                  "pitchDeg": -45, "headingDeg": 120, "rollDeg": 12}}})
    st.record("s", "annotate_map", {"annotations": [
        {"type": "pin", "label": "Tokyo", "latitude": 35.6895, "longitude": 139.6917, "title": "Tokyo"},
        {"type": "pin", "label": "Port", "latitude": 35.62, "longitude": 139.77},
    ]}, {"ok": True, "result": {"items": [
        {"ok": True, "id": "p1", "latitude": 35.6895, "longitude": 139.6917},
        {"ok": True, "id": "p2", "latitude": 35.62, "longitude": 139.77},
    ]}})
    st.record("s", "set_layer_visibility", {"layerId": "flights", "enabled": True}, {"ok": True, "result": {}})
    st.record("s", "hermes_pin_filter", {"action": "set", "categories": ["ramen"], "open_now": True},
              {"ok": True, "result": {"filters": {
                  "categories": ["ramen"], "min_rating": None, "open_now": True,
                  "include_unknown": True, "cluster": True}}})
    st.record("s", "hermes_pin_drawer", {"action": "open"},
              {"ok": True, "result": {"open": True, "pin": {
                  "latitude": 35.6895, "longitude": 139.6917, "label": "Tokyo"}}})
    return st.restore_commands("s", clear=True)


def context_restored_notice(detail: dict | None = None) -> dict:
    """Success is not an error: no ``error`` string, so fix-it annotation leaves it alone."""
    detail = detail if isinstance(detail, dict) else {}
    pins = detail.get("pins")
    extra = ""
    if isinstance(pins, int) and not isinstance(pins, bool) and 0 <= pins <= 500:
        extra = f" Pins restored: {pins}."
    return {
        "ok": True,
        "code": "CONTEXT_RESTORED",
        "message": (
            "WebGL recovery succeeded. The viewer was recreated in the same pane and the saved scene "
            "(camera, pins, annotations, drawer, filters, layers) was restored."
            + extra
        ),
    }
