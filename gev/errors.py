"""Fix-it errors for the model: every failure is {code, message, fix}.

``error`` stays a string (a short code, or the original sentence for policy refusals)
so existing callers that compare ``error == "no_pane"`` keep working. ``fix`` is the
next action the agent should take, not a restatement of the failure.
"""
from __future__ import annotations

import re

# Codes the relay and the tools already emit. ``message`` is the fallback when the
# caller did not supply a more specific one.
TABLE: dict[str, dict[str, str]] = {
    "no_pane": {
        "message": "No Eye Of Hermes (EOH) pane is open in Hermes Desktop or the Webapp.",
        "fix": "Ask the user to open the Eye Of Hermes (EOH) pane and keep it visible, then retry this same call. Data tools (godseye_* without live_) work with no pane.",
    },
    "pane_busy": {
        "message": "The Eye Of Hermes (EOH) pane is open but still busy with an earlier command.",
        "fix": "Wait a few seconds and retry this same call once. Do not send a different live command first, and do not open a second pane.",
    },
    "relay_unavailable": {
        "message": "The live-globe relay is not running.",
        "fix": "Restart the Hermes dashboard or gateway with the godseye plugin enabled, then retry.",
    },
    "timeout": {
        "message": "The Eye Of Hermes (EOH) pane did not answer in time.",
        "fix": "Retry this call once. If it times out again, call godseye_live_panes and godseye_doctor before changing arguments.",
    },
    "MAP_STACK_KEY": {
        "message": "set_map_stack failed because a client map key is missing or not in the pane bundle.",
        "fix": "",  # filled from composer.MAP_STACK_FIX so the text stays in one place
    },
    "KEY_MISSING": {
        "message": "A provider key is missing from the env file or the panel bundle.",
        "fix": "Add the named key to ~/.config/gods-eye-view.env (mode 0600). Never print the value. Then call godseye_rebuild_panel and retry. godseye_doctor lists KEY_MISSING and PANEL_STALE.",
    },
    "PANEL_STALE": {
        "message": "The panel bundle is older than the env file, so client keys are not baked in.",
        "fix": "Call godseye_rebuild_panel (it rebuilds dist/panel with the env file and reloads open panes), then retry.",
    },
    "PANEL_MISSING": {
        "message": "dist/panel is not built.",
        "fix": "Call godseye_rebuild_panel, or run npm run build:panel in the God's Eye View checkout with the env file exported, then reload the pane.",
    },
    "ION_SCOPES": {
        "message": "The Cesium ion token is shipped to the browser but claims more than read access.",
        "fix": "Issue a Cesium ion token with only assets:read and assets:list, put it in ~/.config/gods-eye-view.env, call godseye_rebuild_panel, and retry. Do not print the token.",
    },
    "OVERPASS_NOT_CONFIGURED": {
        "message": "In-pane osm_query has no Overpass server configured.",
        "fix": "",  # poi.remediate builds the OVERPASS_UPSTREAMS line
    },
    "OVERPASS_DOWN": {
        "message": "Every Overpass endpoint failed or rate-limited this call.",
        "fix": "Retry godseye_find_places once after a short wait; it rotates public Overpass mirrors and can fall back to Nominatim. Do not invent places. For in-pane osm_query, set OVERPASS_UPSTREAMS in ~/.config/gods-eye-view.env and restart the app.",
    },
    "OSM_UNAVAILABLE": {
        "message": "Place search could not reach Overpass or Nominatim.",
        "fix": "Retry godseye_find_places once. Do not invent places. If this was osm_query inside the pane, call godseye_find_places instead.",
    },
    "AREA_TOO_LARGE": {
        "message": "The area is too large for the pane's OSM search.",
        "fix": "",  # poi.remediate
    },
    "upstream_down": {
        "message": "The God's Eye View app did not answer.",
        "fix": "Start the app with npm run dev (GODSEYE_API_BASE, default http://localhost:4173) and retry. godseye_doctor checks that it is reachable.",
    },
    "refused": {
        "message": "Refused: this plugin does not search for people, recognise faces, or track individuals.",
        "fix": "Stop. Do not retry with reworded person-tracking arguments. Offer a public-asset view (aircraft, vessels, places, hazards) or ask the user for a place instead.",
    },
    "tool_failed": {
        "message": "The tool failed.",
        "fix": "Read message, fix the arguments, and retry once. If it still fails, call godseye_doctor.",
    },
}

_TOKEN = re.compile(r"token|api[_ ]?key|\bion\b|not authorized|unauthori[sz]ed", re.I)
_KNOWN = set(TABLE)


def _blob(out: dict, res: dict | None) -> str:
    parts = [out.get("code"), out.get("error"), out.get("message")]
    if res:
        parts += [res.get("code"), res.get("error"), res.get("message")]
    return " ".join(str(p) for p in parts if isinstance(p, str))


def _nested(out: dict) -> dict | None:
    res = out.get("result")
    return res if isinstance(res, dict) else None


def needs_fix(out: dict) -> bool:
    """Operational failures only. A doctor report with ok:false is the answer, not an error."""
    if not isinstance(out, dict):
        return False
    if out.get("isError"):
        return True
    if isinstance(out.get("error"), str) and out.get("ok") is not True:
        return True
    if out.get("ok") is False and (out.get("error") or out.get("code")):
        return True
    res = _nested(out)
    return bool(res and res.get("ok") is False and (res.get("error") or res.get("code")))


def classify(out: dict, tool: str | None = None) -> str:
    res = _nested(out)
    blob = _blob(out, res)
    hinted = ""
    for src in (out, res or {}):
        for key in ("code", "error"):
            val = src.get(key)
            if isinstance(val, str) and val in _KNOWN:
                hinted = val
                break
        if hinted:
            break
    if hinted in ("OVERPASS_NOT_CONFIGURED", "AREA_TOO_LARGE", "KEY_MISSING", "PANEL_STALE",
                  "PANEL_MISSING", "ION_SCOPES", "no_pane", "pane_busy", "relay_unavailable", "timeout"):
        return hinted
    if tool == "set_map_stack" and _TOKEN.search(blob):
        return "MAP_STACK_KEY"
    if "OVERPASS_NOT_CONFIGURED" in blob:
        return "OVERPASS_NOT_CONFIGURED"
    if "AREA_TOO_LARGE" in blob:
        return "AREA_TOO_LARGE"
    if "KEY_MISSING" in blob:
        return "KEY_MISSING"
    if "PANEL_STALE" in blob:
        return "PANEL_STALE"
    if "ION_SCOPES" in blob:
        return "ION_SCOPES"
    if re.search(r"overpass", blob, re.I):
        return "OVERPASS_DOWN"
    if hinted == "OSM_UNAVAILABLE" or "OSM_UNAVAILABLE" in blob:
        return "OSM_UNAVAILABLE"
    if hinted in ("no_pane", "pane_busy", "relay_unavailable", "timeout"):
        return hinted
    if "call failed" in blob or "npm run dev" in blob:
        return "upstream_down"
    if "Refused:" in blob or blob.strip().startswith("Refused"):
        return "refused"
    if hinted:
        return hinted
    err = out.get("error") if isinstance(out.get("error"), str) else ""
    if err and len(err) < 40 and " " not in err:
        return err
    return "tool_failed"


def _dynamic_fix(code: str) -> str:
    if code == "MAP_STACK_KEY":
        from .composer import MAP_STACK_FIX
        return MAP_STACK_FIX
    if code in ("OVERPASS_NOT_CONFIGURED", "AREA_TOO_LARGE"):
        from . import poi
        return str(poi.remediate("osm_query", {"code": code}).get("remediation") or "")
    return TABLE.get(code, TABLE["tool_failed"]).get("fix") or TABLE["tool_failed"]["fix"]


def _message(out: dict, res: dict | None, code: str) -> str:
    for src in (out, res or {}):
        msg = src.get("message")
        if isinstance(msg, str) and msg and msg != code:
            return msg
    for src in (out, res or {}):
        err = src.get("error")
        if isinstance(err, str) and err and err != code:
            return err
    return TABLE.get(code, TABLE["tool_failed"])["message"]


def annotate(out: dict, tool: str | None = None) -> dict:
    """Add code, message and fix. Leaves successes, and the original ``error`` string, alone."""
    if not isinstance(out, dict) or not needs_fix(out):
        return out
    out = dict(out)
    res = _nested(out)
    code = classify(out, tool)
    message = _message(out, res, code)
    fix = out.get("fix") or out.get("remediation") or (res or {}).get("remediation") or (res or {}).get("fix")
    if not fix:
        fix = _dynamic_fix(code)
    out["code"] = code
    out["message"] = message
    out["fix"] = fix
    if not isinstance(out.get("error"), str) or not out["error"]:
        # Policy refusals keep their sentence in ``error`` so "Refused" checks still match.
        err = None
        if res and isinstance(res.get("error"), str):
            err = res["error"]
        out["error"] = err or (message if code == "refused" else code)
    if code == "refused" and isinstance(out.get("error"), str) and "Refused" not in out["error"]:
        out["error"] = message if message.startswith("Refused") else f"Refused: {message}"
    return out
