"""Backend routes (/api/plugins/godseye/*) for the Desktop pane: status and Hermes-powered HUD."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from fastapi import APIRouter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gev import bridge, config, hud, mcp_client  # noqa: E402

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
    return {"apiBase": config.api_base(), "frameUrl": config.public_url(), "appUp": up, "hudBridge": bridge._server is not None,
            "checkout": config.checkout() is not None, "node": config.node_bin() is not None}


@router.post("/hud")
async def hud_summary(body: dict):
    import asyncio
    try:
        return await asyncio.to_thread(hud.summarize, _plugin_llm(), body.get("context") or {})
    except Exception as exc:
        return {"summary": None, "feedState": None, "error": str(exc)[:300]}


GLOBE_PANEL_URI = "ui://gods-eye-view/globe"
# The pane is an MCP Apps host for God's Eye View's own globe panel. The panel runs in the
# SDK's opaque-origin SandboxedFrame and loads the app only through these two tools.
PANEL_TOOLS = {"panel_request", "show_in_gods_eye_view"}


@router.get("/panel")
async def panel():
    import asyncio
    result = await asyncio.to_thread(mcp_client.client().read_resource, GLOBE_PANEL_URI)
    item = (result or {}).get("contents", [{}])[0]
    return {"html": item.get("text", ""), "mimeType": item.get("mimeType")}


@router.post("/tool")
async def tool(body: dict):
    import asyncio
    name = body.get("name")
    if name not in PANEL_TOOLS:
        return {"isError": True, "content": [{"type": "text", "text": "tool not allowed from the pane"}]}
    try:
        return await asyncio.to_thread(mcp_client.client().call_tool, name, body.get("arguments") or {})
    except Exception as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)[:300]}]}
