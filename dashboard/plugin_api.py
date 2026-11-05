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
try:  # live-globe relay between Hermes tools and the open pane
    bridge.start_live_relay()
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
            "checkout": config.checkout() is not None, "node": config.node_bin() is not None,
            "patched": config.patched()}


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


def _panel_config(perf: str | None):
    """Rewrite config.json and build the gev:config body. Field names only in the loggable parts."""
    from gev import config, runtime_config
    from gev.composer import env_file
    from gev.gl_recover import enabled as gl_recover_enabled
    written = runtime_config.write_checkout()
    mode = runtime_config.perf_mode(perf)
    root = config.checkout()
    overlay = runtime_config.overlay_from_env(runtime_config.merged_env(root, env_file())) if root else {}
    gl_on = gl_recover_enabled()
    return {
        "configMessage": runtime_config.config_message(overlay, mode),
        "configFields": written.get("fields") or [],
        "configRev": runtime_config.revision(overlay, mode, {"glRecover": gl_on}),
        "glRecover": gl_on,
        "_overlay": overlay,
        "_mode": mode,
        "_glRecover": gl_on,
    }


@router.get("/panel")
async def panel(fullUi: bool = True, perf: str | None = None, configOnly: bool = False):
    import asyncio
    from gev import panel_inject

    def _build():
        meta = _panel_config(perf)
        public = {"configMessage": meta["configMessage"], "configFields": meta["configFields"],
                  "configRev": meta["configRev"], "glRecover": meta["glRecover"]}
        if configOnly:
            return public
        result = mcp_client.client().read_resource(GLOBE_PANEL_URI)
        item = (result or {}).get("contents", [{}])[0]
        html = item.get("text", "")
        prepared = panel_inject.prepare(html, full_ui=fullUi, perf=meta["_mode"], config=meta["_overlay"],
                                        gl_recover=meta["_glRecover"]) if html else ""
        return {"html": prepared, "mimeType": item.get("mimeType"), **public}

    return await asyncio.to_thread(_build)


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


@router.get("/live/next")
async def live_next(pane: str = "", sessions: str = "", fresh: str = "0", label: str = ""):
    import asyncio
    import urllib.parse
    from gev import live
    q = urllib.parse.urlencode({k: v for k, v in {
        "pane": pane[:80], "sessions": sessions[:1000], "fresh": fresh[:1], "label": label[:80]
    }.items() if v and not (k == "fresh" and v == "0")})
    return await asyncio.to_thread(live.call, "/next" + (f"?{q}" if q else ""), bridge.ensure_token(), None,
                                   live.POLL_WAIT_S + 10)


@router.get("/live/panes")
async def live_panes():
    import asyncio
    from gev import live
    return await asyncio.to_thread(live.call, "/status", bridge.ensure_token(), None, 5)


@router.post("/live/panes")
async def live_panes_op(body: dict):
    import asyncio
    from gev import live
    return await asyncio.to_thread(live.call, "/panes", bridge.ensure_token(), body or {}, 10)


@router.get("/home")
async def home_view():
    from gev import home
    saved = home.get()
    return {"home": saved, "initialView": home.initial_view(saved) if saved else None}


@router.get("/doctor")
async def doctor():
    import asyncio
    from gev import composer
    return await asyncio.to_thread(composer.doctor)


@router.post("/live/result")
async def live_result(body: dict):
    import asyncio
    from gev import live
    return await asyncio.to_thread(live.call, "/result", bridge.ensure_token(), body, 15)


@router.post("/live/filters")
async def live_filters(body: dict):
    import asyncio
    from gev import live
    return await asyncio.to_thread(live.call, "/filters", bridge.ensure_token(), body, 15)


@router.post("/live/drawer")
async def live_drawer(body: dict):
    import asyncio
    from gev import live
    return await asyncio.to_thread(live.call, "/drawer", bridge.ensure_token(), body, 15)


@router.post("/live/state")
async def live_state(body: dict):
    """Pane heartbeat: compact map snapshot for the per-turn line. No secrets."""
    import asyncio
    from gev import live
    return await asyncio.to_thread(live.call, "/state", bridge.ensure_token(), body or {}, 5)


@router.post("/live/gl")
async def live_gl(body: dict):
    """WebGL loss, recovery, or a request for restore_commands. No secrets."""
    import asyncio
    from gev import live
    return await asyncio.to_thread(live.call, "/gl", bridge.ensure_token(), body or {}, 15)
