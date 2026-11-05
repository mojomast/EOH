"""Settings from env (never secrets): where the God's Eye View checkout and app live."""
import os
import shutil
from pathlib import Path

DEFAULT_API_BASE = "http://localhost:4173"


def api_base() -> str:
    return os.environ.get("GODSEYE_API_BASE", DEFAULT_API_BASE).rstrip("/")


def checkout() -> Path | None:
    raw = os.environ.get("GODSEYE_HOME", "")
    path = Path(raw).expanduser() if raw else Path.home() / "gods-eye-view"
    return path if (path / "server/mcp/stdio.js").is_file() else None


def node_bin() -> str | None:
    raw = os.environ.get("GODSEYE_NODE")
    if raw and Path(raw).expanduser().is_file():
        return str(Path(raw).expanduser())
    local = Path.home() / ".local/node24/bin/node"
    return str(local) if local.is_file() else shutil.which("node")


def public_url() -> str:
    """URL the Desktop/Webapp pane frames: GODSEYE_PUBLIC_URL (e.g. a tailnet address when the
    Webapp runs in a browser on another machine), else the API base."""
    return os.environ.get("GODSEYE_PUBLIC_URL", "").rstrip("/") or api_base()


def patched() -> dict:
    """Which optional upstream features (patches/) the God's Eye View checkout has."""
    root = checkout()

    def has(rel: str, needle: str) -> bool:
        try:
            return needle in (root / rel).read_text(errors="ignore")
        except Exception:
            return False
    return {"fullUi": bool(root) and has("src/app/embed.js", "GEV_EMBED_FULL_UI"),
            "hud": bool(root) and has("server/providers/openai/hud-summary.js", "GEV_HUD_SUMMARY_BASE_URL")}
