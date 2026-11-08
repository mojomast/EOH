"""Saved home/default view: ctx.state when the host has it, mirrored to a plugin-data file so the
dashboard backend (which has no plugin ctx) and older Hermes builds read the same value."""
from __future__ import annotations

import json
import os
from pathlib import Path

KEY = "home_view"
STYLES = {"normal", "retro", "surveillance", "thermal", "anime", "noir"}
DEFAULT = {"lat": 20.0, "lon": 0.0, "altitude_m": 15_000_000, "layers": ["flights"], "style": "normal"}
_state = None  # ctx.state facade (hermes_cli.plugins_state.PluginState) when registered


def bind(state) -> None:
    global _state
    _state = state


def _file() -> Path:
    from hermes_constants import get_hermes_home
    home = Path(get_hermes_home())
    return home / "plugin-data" / "godseye" / "home.json"


def normalize(view: dict) -> dict:
    out = dict(DEFAULT)
    view = view or {}
    lat, lon = float(view.get("lat", out["lat"])), float(view.get("lon", out["lon"]))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat/lon out of range")
    out.update(lat=round(lat, 5), lon=round(lon, 5),
               altitude_m=max(100, min(40_000_000, int(float(view.get("altitude_m") or out["altitude_m"])))))
    layers = view.get("layers", out["layers"])
    if isinstance(layers, str):
        layers = [x for x in layers.replace(",", " ").split() if x]
    out["layers"] = [str(x)[:64] for x in layers][:16]
    style = str(view.get("style") or "normal")
    out["style"] = style if style in STYLES else "normal"
    return out


def get() -> dict:
    """The saved home view, or None when none was saved."""
    if _state is not None:
        try:
            val = _state.get(KEY)
            if val:
                return normalize(val)
        except Exception:
            pass
    try:
        return normalize(json.loads(_file().read_text()))
    except Exception:
        return None


def get_or_default() -> dict:
    return get() or dict(DEFAULT)


def set(view: dict) -> dict:  # noqa: A001 - mirrors ctx.state.set
    val = normalize(view)
    if _state is not None:
        try:
            _state.set(KEY, val)
        except Exception:
            pass
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(val))
    tmp.replace(path)
    return val


def initial_view(view: dict | None = None) -> dict:
    """show_in_gods_eye_view arguments the pane uses on cold start."""
    v = view or get_or_default()
    return {"camera": {"lat": v["lat"], "lon": v["lon"], "altitude_m": v["altitude_m"]},
            "style": v["style"], "layers": v["layers"]}
