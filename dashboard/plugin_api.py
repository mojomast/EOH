"""Backend routes (/api/plugins/godseye/*) for the Desktop pane: status and Hermes-powered HUD."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from fastapi import APIRouter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gev import bridge, config, hud  # noqa: E402

try:  # serve God's Eye View's own HUD (GEV_HUD_SUMMARY_BASE_URL) from this Hermes process
    bridge.start_in_thread()
except Exception:
    pass

router = APIRouter()
_llm = None


def _plugin_llm():
    global _llm
    if _llm is None:
        from agent.plugin_llm import PluginLlm
        _llm = PluginLlm(plugin_id="godseye")
    return _llm


@router.get("/status")
async def status():
    up = False
    try:
        with urllib.request.urlopen(config.api_base() + "/", timeout=3) as r:
            up = r.status == 200
    except Exception:
        pass
    return {"apiBase": config.api_base(), "appUp": up, "hudBridge": bridge._server is not None,
            "checkout": config.checkout() is not None, "node": config.node_bin() is not None}


@router.post("/hud")
async def hud_summary(body: dict):
    import asyncio
    try:
        return await asyncio.to_thread(hud.summarize, _plugin_llm(), body.get("context") or {})
    except Exception as exc:
        return {"summary": None, "feedState": None, "error": str(exc)[:300]}
