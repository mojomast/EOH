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
    "pane_lost": {
        "message": "The Eye Of Hermes (EOH) pane stopped responding.",
        "fix": "Ask the user to reopen the Eye Of Hermes (EOH) pane and keep it visible. The saved scene restores on its own. If a route walk or dolly was running, it was interrupted; offer to resume it.",
    },
    "MOTION_INTERRUPTED": {
        "message": "A route walk or dolly was interrupted when the Eye Of Hermes pane reloaded.",
        "fix": "Ask the user to keep the pane open. Offer to resume a street walk with godseye_nav op=street_walk walk=resume, or start the dolly again with godseye_nav op=fly_route.",
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
        "fix": "Retry this call once. If it times out again, call godseye_view op=panes and godseye_admin op=doctor before changing arguments.",
    },
    "CONTEXT_LOST": {
        "message": "The Eye Of Hermes (EOH) pane lost its WebGL context. The viewer is being destroyed and created again in the same pane, then the saved scene is restored.",
        "fix": "Wait for automatic recovery. Camera heading, pitch, and roll, plus pins, annotations, the drawer, filters, and layers come back from the saved scene. If a Click to reload the globe card appears, ask the user to click it once. Do not rebuild the panel and do not open a second pane.",
    },
    "MAP_STACK_KEY": {
        "message": "set_map_stack failed because a client map key is missing or not in the pane bundle.",
        "fix": "",  # filled from composer.MAP_STACK_FIX so the text stays in one place
    },
    "KEY_MISSING": {
        "message": "A provider key is missing from the env file or the panel bundle.",
        "fix": "Add the named key to ~/.config/gods-eye-view.env (mode 0600). Never print the value. Client keys are written to dist/panel/config.json when the pane is served, so a current God's Eye View build does not need a rebuild. Older builds still need godseye_admin op=rebuild. godseye_admin op=doctor lists KEY_MISSING and PANEL_STALE.",
    },
    "PANEL_STALE": {
        "message": "The panel bundle is older than the env file, so client keys are not baked in.",
        "fix": "On a current God's Eye View build, open the Eye Of Hermes pane so dist/panel/config.json is rewritten from the env file (no rebuild). On an older build, call godseye_admin op=rebuild and reload the pane.",
    },
    "PANEL_MISSING": {
        "message": "dist/panel is not built.",
        "fix": "Call godseye_admin op=rebuild, or run npm run build:panel in the God's Eye View checkout with the env file exported, then reload the pane.",
    },
    "ION_SCOPES": {
        "message": "The Cesium ion token is shipped to the browser but claims more than read access.",
        "fix": "Issue a Cesium ion token with only assets:read and assets:list, put it in ~/.config/gods-eye-view.env, call godseye_admin op=rebuild, and retry. Do not print the token.",
    },
    "OVERPASS_NOT_CONFIGURED": {
        "message": "In-pane osm_query has no Overpass server configured.",
        "fix": "",  # poi.remediate builds the OVERPASS_UPSTREAMS line
    },
    "OVERPASS_DOWN": {
        "message": "Every Overpass endpoint failed or rate-limited this call.",
        "fix": "Retry godseye_query op=find_places once after a short wait; it rotates public Overpass mirrors and can fall back to Nominatim. Do not invent places. For in-pane osm, set OVERPASS_UPSTREAMS in ~/.config/gods-eye-view.env and restart the app.",
    },
    "OSM_UNAVAILABLE": {
        "message": "Place search could not reach Overpass or Nominatim.",
        "fix": "Retry godseye_query op=find_places once. Do not invent places. If this was osm inside the pane, call godseye_query op=find_places instead.",
    },
    "AREA_TOO_LARGE": {
        "message": "The area is too large for the pane's OSM search.",
        "fix": "",  # poi.remediate
    },
    "upstream_down": {
        "message": "The God's Eye View app did not answer.",
        "fix": "Start the app with npm run dev (GODSEYE_API_BASE, default http://localhost:4173) and retry. godseye_admin op=doctor checks that it is reachable.",
    },
    "refused": {
        "message": "Refused: this plugin does not search for people, recognise faces, or track individuals.",
        "fix": "Stop. Do not retry with reworded person-tracking arguments. Offer a public-asset view (aircraft, vessels, places, hazards) or ask the user for a place instead.",
    },
    "tool_failed": {
        "message": "The tool failed.",
        "fix": "Read message, fix the arguments, and retry once. If it still fails, call godseye_admin op=doctor.",
    },
    "routing_unavailable": {
        "message": "The public routing service did not answer.",
        "fix": "Retry once. For a straight-line distance use godseye_nav op=measure with measure=distance (not measure=route). Do not invent a travel time or draw a circle as a substitute for an isochrone. Optionally set GODSEYE_OSRM_URL or GODSEYE_VALHALLA_URL.",
    },
    "wayback_unavailable": {
        "message": "Esri World Imagery Wayback config is unreachable.",
        "fix": "Retry godseye_nav op=compare action=list_dates once. If it still fails, compare left=osm or left=current instead of a wayback date.",
    },
    "no_dates": {
        "message": "No Esri Wayback release falls in that window.",
        "fix": "Call godseye_nav op=timeline with timeline=list_dates and set from and to inside the returned dates.",
    },
    "no_timeline": {
        "message": "No time slider is saved.",
        "fix": "Call godseye_nav op=timeline with timeline=set and mode=wayback or mode=events, then play or step.",
    },
    "no_focus": {
        "message": "There is no focus point on the pane.",
        "fix": "Pass lat and lon, or ask the user to click a pin or the spot, then retry. godseye_query op=context shows the current focus.",
    },
    "no_drawing": {
        "message": "No drawn shape is on the pane.",
        "fix": "Ask the user to draw a line, box or polygon, or pass points (or pins) to godseye_nav op=measure instead of use_drawn.",
    },
    "pin_not_found": {
        "message": "No pin on the pane matched that id or label.",
        "fix": "Call godseye_annotate op=list or godseye_query op=context and retry with an id or label from that list.",
    },
    "unsupported_side": {
        "message": "That compare side is a map stack, which cannot be split.",
        "fix": "Use left/right of current, osm, or wayback:YYYY[-MM]. Map stacks cannot be split.",
    },
    "confirm_required": {
        "message": "Refusing to delete without confirm=true.",
        "fix": "Call the delete tool again with the same name and confirm=true only if the user asked to delete it.",
    },
    "CONFIRM_REQUIRED": {
        "message": "Deleting this drops saved state. Confirm with the user first.",
        "fix": "Ask the user, then call godseye_watch op=delete (or godseye_scene op=delete) again with the same id and confirm=true.",
    },
    "not_found": {
        "message": "Nothing with that name or id was found.",
        "fix": "Read message for which list to use. Investigations: godseye_scene op=list. Pins: godseye_annotate op=list. Watches: godseye_watch op=list. Retry with a name or id from that list.",
    },
    "NOT_FOUND": {
        "message": "No watch with that id.",
        "fix": "Call godseye_watch op=list and retry with an id from that list.",
    },
    "no_pins": {
        "message": "No matching pins are in this scene.",
        "fix": "Pin the places first (godseye_query op=find_places pin=true or godseye_annotate op=annotate), then call godseye_annotate op=research. Pass ids from godseye_annotate op=list if you named specific pins.",
    },
    "geocoder_unavailable": {
        "message": "Nominatim reverse geocoding failed.",
        "fix": "Retry godseye_query op=context or op=what_is_here once. Do not invent the place name.",
    },
    "osm_unavailable": {
        "message": "Nearby place lookup failed.",
        "fix": "Retry once. If Overpass is down, call godseye_query op=what_is_here with nearby=false for the reverse geocode only, or use godseye_query op=find_places which rotates public mirrors.",
    },
    "no_match": {
        "message": "No named place at this coordinate.",
        "fix": "Use the lat/lon as given. nearby places may still name what is around the point.",
    },
    "bad_points": {
        "message": "The points do not fit this measurement.",
        "fix": "Pass at least 2 {lat, lon} points for distance or route, or 3+ for area. Or pass pins / use_drawn from godseye_query op=context.",
    },
    "bad_args": {
        "message": "The arguments are not valid.",
        "fix": "Read message and retry with lat/lon in range, a valid area, and mode walk, bike or drive.",
    },
    "bad_mode": {
        "message": "mode must be walk, bike or drive.",
        "fix": "Retry with mode set to walk, bike or drive.",
    },
    "bad_focus": {
        "message": "The focus point could not be read.",
        "fix": "Pass lat and lon, or click the spot on the globe, then retry.",
    },
    "BAD_AREA": {
        "message": "The watch area is not valid.",
        "fix": "Pass area as {bbox:[south,west,north,east]}, {polygon:[[lat,lon],...]}, {lat,lon,radius_km}, or {view:true} with the Eye Of Hermes (EOH) pane open.",
    },
    "BAD_FEEDS": {
        "message": "The watch feeds are not valid.",
        "fix": "feeds must be one or more of earthquakes, fires, flights, ships, pois.",
    },
    "invalid_geojson": {
        "message": "The export did not validate as GeoJSON (RFC 7946).",
        "fix": "Retry godseye_scene op=export. If it fails again, export a saved investigation by name from godseye_scene op=list.",
    },
    "invalid_kml": {
        "message": "The export did not validate as KML.",
        "fix": "Retry godseye_scene op=export. If it fails again, export format=geojson only and say the KML step failed.",
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
                  "PANEL_MISSING", "ION_SCOPES", "no_pane", "pane_lost", "pane_busy", "relay_unavailable", "timeout",
                  "CONTEXT_LOST", "MOTION_INTERRUPTED",
                  "routing_unavailable", "wayback_unavailable", "no_dates", "no_timeline", "no_focus", "no_drawing", "pin_not_found",
                  "unsupported_side", "confirm_required", "CONFIRM_REQUIRED", "not_found", "NOT_FOUND",
                  "no_pins", "geocoder_unavailable", "osm_unavailable", "no_match", "bad_points",
                  "bad_args", "bad_mode", "bad_focus", "BAD_AREA", "BAD_FEEDS"):
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
