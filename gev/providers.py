"""Keyed-provider probes and env-file key install.

A probe makes one cheap call and returns ok, invalid, quota, unreachable, or
missing. Key values are never returned, logged, or copied into an error.
``op=keys`` reads ``KEY=VALUE`` from a path and writes allowed names into the
env file mode 0600.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import urlencode

from .composer import CLIENT_KEYS, SERVER_KEYS, env_file, read_env, scrub_text

TOMTOM_KEY = "TOMTOM_API_KEY"
ALLOWED_KEYS = tuple(dict.fromkeys((*CLIENT_KEYS.keys(), *SERVER_KEYS.keys(), TOMTOM_KEY)))
_SECRET_CHARS = 6


def _key(name: str, file_env: dict) -> str:
    return str(file_env.get(name) or os.environ.get(name) or "")


def classify_probe(status, body: str = "") -> str:
    text = scrub_text(body or "").lower()
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


def _get(url: str, headers: dict | None, fetch, timeout: float = 4) -> tuple[int | None, str]:
    try:
        got = fetch(url, headers or {}, timeout)
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
    return {"provider": provider, "status": status}


def probe_providers(fetch=None, file_env: dict | None = None, api_base: str | None = None) -> list[dict]:
    """One cheap call per keyed provider. The result has no key material."""
    from . import config
    env = file_env if file_env is not None else read_env(env_file())
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
    request = urllib.request.Request(url, method="GET", headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return getattr(resp, "status", 200), resp.read(400)
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(400)
        except Exception:
            raw = b""
        return exc.code, raw
    except Exception:
        return None, b""


def _parse_env_text(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            found[key] = value
    return found


def install_keys(path: str, *, env_path: Path | None = None, restart=None, regen=None,
                 probe=None, invalidate=None) -> dict:
    """Read a KEY=VALUE file. Write allowed keys. Never echo values."""
    raw = str(path or "").strip()
    if not raw or "://" in raw:
        return {"ok": False, "error": "bad_path",
                "message": "Pass file as a path. Values are not accepted in the tool call.",
                "fix": "Call godseye_admin op=keys with action=install and file set to a path."}
    file = Path(raw).expanduser()
    if not file.is_file():
        return {"ok": False, "error": "not_found", "message": "Key file was not found.",
                "fix": "Pass the path of a KEY=VALUE file. Do not paste the values."}
    try:
        if file.stat().st_size > 64_000:
            return {"ok": False, "error": "too_large", "message": "Key file is too large.",
                    "fix": "Keep the file to the provider keys only."}
        parsed = _parse_env_text(file.read_text(encoding="utf-8"))
    except OSError:
        return {"ok": False, "error": "unreadable", "message": "Key file could not be read.",
                "fix": "Check the path. Do not paste the values into the tool call."}
    installed, skipped = [], []
    chosen = {}
    for key, value in parsed.items():
        if key in ALLOWED_KEYS and value.strip():
            chosen[key] = value
            installed.append(key)
        elif key:
            skipped.append(key)
    if not installed:
        return {"ok": False, "error": "no_keys", "message": "The file had none of the provider key names.",
                "fix": "Use names such as GOOGLE_MAPS_API_KEY, MAPILLARY_CLIENT_TOKEN, "
                       "AISSTREAM_API_KEY, FIRMS_MAP_KEY, or TOMTOM_API_KEY. Do not paste values here.",
                "skipped": skipped[:20]}
    target = env_path or env_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        current = read_env(target)
        current.update(chosen)
        lines = [f"{key}={current[key]}" for key in sorted(current)]
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, ("\n".join(lines) + "\n").encode())
        finally:
            os.close(fd)
        os.chmod(target, 0o600)
        mode = oct(stat.S_IMODE(target.stat().st_mode))
    except OSError:
        return {"ok": False, "error": "write_failed", "message": "The env file could not be written.",
                "fix": "Check the directory of the env file. Nothing was echoed."}
    for key, value in chosen.items():
        os.environ[key] = value
    regenerated = None
    if regen is not None:
        try:
            regenerated = regen()
        except Exception:
            regenerated = {"ok": False, "error": "config_write_failed"}
    restarted = None
    if restart is not None:
        try:
            restarted = restart()
        except Exception:
            restarted = {"ok": False, "error": "restart_failed"}
    if invalidate is not None:
        try:
            invalidate()
        except Exception:
            pass
    probes = []
    if probe is not None:
        try:
            probes = probe()
        except Exception:
            probes = []
    result = {
        "ok": True,
        "action": "install",
        "installed": installed,
        "skipped": skipped[:20],
        "env": str(target),
        "mode": mode,
        "probes": probes,
        "cache": "reload the pane" if TOMTOM_KEY in installed else None,
    }
    if regenerated is not None:
        result["config"] = {k: regenerated.get(k) for k in ("ok", "paths", "fields", "error") if isinstance(regenerated, dict)}
    if isinstance(restarted, dict):
        result["restarted"] = bool(restarted.get("ok"))
    text = str(result)
    for value in chosen.values():
        if len(value) >= _SECRET_CHARS and value in text:
            result = {"ok": False, "error": "redaction_failed", "message": "The result was dropped because it contained a secret."}
            break
    return result
