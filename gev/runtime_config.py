"""Runtime client config for a God's Eye View build that reads it.

``dist/panel/config.json`` (and ``public/config.json`` in a dev checkout) is
rewritten from the env file when the pane is served or the panel is rebuilt.
A key change does not need ``npm run build:panel`` on a build that fetches
that file or accepts ``gev:config``. Values are never logged or returned.

The file lives in the God's Eye View checkout, which gitignores it. This
module does not write into the plugin repo.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

PERF_ENV = "GODSEYE_PERF"

# Env name -> config.json / gev:config field. Photon may be empty (disabled).
_FIELDS = (
    ("GOOGLE_MAPS_API_KEY", "googleMapsApiKey"),
    ("CESIUM_ION_TOKEN", "cesiumIonToken"),
    ("MAPILLARY_CLIENT_TOKEN", "mapillaryToken"),
    ("PHOTON_URL", "photonUrl"),
)
_SECRET_FIELDS = {"googleMapsApiKey", "cesiumIonToken", "mapillaryToken", "photonUrl"}


def perf_mode(raw: str | None = None) -> str | None:
    """``embed`` by default. ``0`` / ``off`` / ``default`` leaves the normal viewer."""
    if raw is None:
        raw = os.environ.get(PERF_ENV, "embed")
    text = str(raw if raw is not None else "").strip().lower()
    if text in {"0", "off", "no", "false", "default", ""}:
        return None
    return "embed"


def _clean(value) -> str:
    return str(value or "").strip()


def overlay_from_env(env: dict | None) -> dict:
    """Recognized fields only. Empty keys are omitted; an explicit Photon off is kept."""
    env = env or {}
    out: dict = {}
    for env_key, field in _FIELDS:
        if env_key not in env:
            continue
        raw = env.get(env_key)
        if field == "photonUrl":
            text = _clean(raw)
            out[field] = "" if text.lower() in {"", "off", "none", "disabled"} else text
            continue
        text = _clean(raw)
        if text:
            out[field] = text
    return out


def merged_env(root: Path | None, envf: Path | None) -> dict:
    """Repo ``.env``, then the env file, then process env. Values stay in this dict only."""
    from .composer import read_env
    out: dict = {}
    if root is not None:
        out.update(read_env(root / ".env"))
    if envf is not None:
        out.update(read_env(envf))
    for env_key, _field in _FIELDS:
        if os.environ.get(env_key):
            out[env_key] = os.environ[env_key]
    return out


def config_paths(root: Path) -> list[Path]:
    """Where a served page would fetch ``config.json``. Missing dirs are skipped."""
    found = []
    panel = root / "dist" / "panel"
    if panel.is_dir():
        found.append(panel / "config.json")
    public = root / "public"
    if public.is_dir() and (root / "index.html").is_file():
        found.append(public / "config.json")
    return found


def read_config(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def write_files(root: Path, env: dict) -> dict:
    """Rewrite config files. The result lists paths and field names, never values."""
    if root is None or not root.is_dir():
        return {"ok": False, "error": "no checkout", "paths": [], "fields": []}
    overlay = overlay_from_env(env)
    paths = config_paths(root)
    written = []
    for path in paths:
        _atomic_write(path, overlay)
        written.append(str(path))
    return {"ok": True, "paths": written, "fields": sorted(overlay)}


def write_checkout(root: Path | None = None, envf: Path | None = None) -> dict:
    from . import config
    from .composer import env_file
    root = root or config.checkout()
    if root is None:
        return {"ok": False, "error": "no checkout", "paths": [], "fields": []}
    return write_files(root, merged_env(root, envf or env_file()))


def supports_runtime(root: Path | None, bundle_text: str = "") -> bool:
    """True when this checkout understands config.json / gev:config."""
    markers = ("gev:config", "GEV_RUNTIME_CONFIG", "waitForConfig")
    if bundle_text and any(m in bundle_text for m in markers):
        return True
    if root is None:
        return False
    candidates = [
        root / "dist" / "panel" / "embed-boot.js",
        root / "dist" / "panel" / "index.html",
        root / "public" / "embed-boot.js",
        root / "src" / "runtimeConfig.js",
    ]
    for path in candidates:
        try:
            if path.is_file() and "gev:config" in path.read_text(errors="ignore"):
                return True
        except OSError:
            continue
    return False


def revision(overlay: dict | None, perf: str | None, extra: dict | None = None) -> str:
    """Cache key for a prepared panel. A hash, so the value is not recoverable from it."""
    payload = {"config": overlay or {}, "perf": perf}
    if extra:
        payload["extra"] = extra
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def config_message(overlay: dict | None, perf: str | None) -> dict:
    """Body of a ``gev:config`` postMessage. Nested ``config`` is included too."""
    body = {k: v for k, v in (overlay or {}).items() if k in _SECRET_FIELDS or k == "perf"}
    msg = {"type": "gev:config", **body}
    if perf in {"embed", "default"}:
        msg["perf"] = perf
    if body:
        msg["config"] = dict(body)
    return msg


def scrub(text: str, env: dict | None) -> str:
    """Drop key values from a log line."""
    out = text or ""
    for value in (env or {}).values():
        if isinstance(value, str) and len(value) >= 8:
            out = out.replace(value, "***")
    return out


def file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)
