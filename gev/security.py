"""Small security boundaries shared by plugin entry points.

Credentials are resolved by Hermes, never by reading dotenv files ourselves.
External import folders are host-configured, not granted by model tool arguments.
"""
from __future__ import annotations

import os
from pathlib import Path

SECRET_NAMES = (
    "GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY", "MAPILLARY_CLIENT_TOKEN",
    "CESIUM_ION_TOKEN", "AISSTREAM_API_KEY", "FIRMS_MAP_KEY", "FIRMS_API_KEY",
    "FIRMS_KEY", "NASA_FIRMS_KEY", "TOMTOM_API_KEY",
)


def secret(name: str) -> str:
    if name not in SECRET_NAMES:
        return ""
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        return ""  # No fallback to process env outside a Hermes host.
    value = get_secret(name)
    if not value and name == "FIRMS_MAP_KEY":
        value = next((v for alias in ("FIRMS_API_KEY", "FIRMS_KEY", "NASA_FIRMS_KEY") if (v := get_secret(alias))), None)
    return str(value or "")


def secrets(names=SECRET_NAMES) -> dict[str, str]:
    return {name: value for name in names if (value := secret(name))}


def plugin_data() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()) / "plugin-data" / "godseye"


# Exact names only: no prefix-wide NODE_* / GEV_* forwarding (which could copy
# unrelated credentials or NODE_OPTIONS startup injection from the host).
_CHILD_SETTINGS = (
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT",
    "NODE_ENV", "NODE_EXTRA_CA_CERTS", "NODE_USE_SYSTEM_CA",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "GEV_PORT", "GEV_HOST", "GEV_BIND", "GEV_DATA_DIR", "OVERPASS_UPSTREAMS",
    "GEV_EMBED_FULL_UI", "GEV_ALLOWED_HOSTS", "GEV_HUD_SUMMARY_BASE_URL",
    "GEV_HUD_SUMMARY_MODEL",
    "PHOTON_URL", "GODSEYE_PERF",
)


def child_env(node: str | None = None, *, app_keys=()) -> dict[str, str]:
    env = {name: os.environ[name] for name in _CHILD_SETTINGS if name in os.environ}
    env.update(secrets(app_keys))
    if node:
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", os.defpath)
    return env


def credential_name(path: Path) -> bool:
    """True for env, key, and token files that tool imports must not read."""
    name = path.name.lower()
    if name.startswith(".env") or name.endswith((".env", ".pem", ".key")):
        return True
    if name in {"hud-bridge.env", "auth.json", "credentials.json", "secrets.json"}:
        return True
    if name.startswith(("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")):
        return True
    return False


def import_file(source: str, *, default_root: Path | None = None) -> Path:
    """Resolve symlinks before enforcing plugin-data or a host-approved folder.

    GODSEYE_IMPORT_ROOT is set by the user in the host environment, never from
    a tool argument. Relative sources are relative to default_root/plugin-data.
    Credential-like names (``.env``, ``hud-bridge.env``, key files) are refused
    even when they sit inside an approved folder.
    """
    raw = str(source or "").strip()
    if not raw or "://" in raw:
        raise ValueError("Import source must be a local file under plugin-data/godseye or GODSEYE_IMPORT_ROOT.")
    root = plugin_data().resolve()
    path = Path(raw).expanduser()
    path = ((default_root or root) / path if not path.is_absolute() else path).resolve()
    roots = [root]
    approved = os.environ.get("GODSEYE_IMPORT_ROOT", "").strip()
    if approved:
        folder = Path(approved).expanduser()
        if not folder.is_absolute() or folder.resolve() == Path(folder.anchor):
            raise ValueError("GODSEYE_IMPORT_ROOT must name a specific absolute folder, not a filesystem root.")
        roots.append(folder.resolve())
    if not any(path.is_relative_to(folder) for folder in roots):
        raise ValueError("Import source must stay under plugin-data/godseye or the user-configured GODSEYE_IMPORT_ROOT.")
    if credential_name(path):
        raise ValueError("Refusing to import a credential file.")
    return path
