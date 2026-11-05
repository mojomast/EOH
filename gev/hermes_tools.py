"""Register God's Eye View's MCP catalog as Hermes tools, plus a Hermes-inference HUD tool."""
from __future__ import annotations

import json
from pathlib import Path

from . import config, detail, errors, hud, mcp_client, policy, turn_state

TOOLSET = "godseye"
SNAPSHOT = Path(__file__).with_name("tools_snapshot.json")


def load_catalog() -> list[dict]:
    return [t for t in json.loads(SNAPSHOT.read_text()) if t["name"] not in policy.EXCLUDED_TOOLS]


def _available() -> bool:
    return config.checkout() is not None and config.node_bin() is not None


def _format(result: dict) -> str:
    parts = [c.get("text", "") for c in (result or {}).get("content", []) if c.get("type") == "text"]
    text = "\n".join(p for p in parts if p) or json.dumps(result.get("structuredContent", {}))
    if result.get("isError"):
        return json.dumps({"error": text[:4000]})
    return text[:20000]


def _present_error(payload: dict, tool: str) -> str:
    return json.dumps(errors.annotate(payload, tool=tool), ensure_ascii=False)


def make_handler(name: str):
    def handler(args: dict, **_kw) -> str:
        args = dict(args or {})
        mode = detail.pop_detail(args) if name in detail.LARGE_DATA else "full"
        reason = policy.refuse_reason(args)
        if reason:
            return _present_error({"ok": False, "error": reason}, name)
        try:
            result = mcp_client.client().call_tool(name, args)
        except Exception as exc:  # surfaced to the model, never raised into the turn
            return _present_error({"ok": False, "error": f"God's Eye View call failed: {exc}. Is `npm run dev` running at {config.api_base()}?"}, name)
        if name in detail.LARGE_DATA:
            return detail.render_mcp(name, result, mode)
        text = _format(result)
        if (result or {}).get("isError"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = {"ok": False, "error": text[:2000]}
            return _present_error(parsed if isinstance(parsed, dict) else {"ok": False, "error": text[:2000]}, name)
        return text
    return handler


LIVE_SNAPSHOT = Path(__file__).with_name("live_tools_snapshot.json")
LIVE_PREFIX = "godseye_live_"

SYSTEM_PROMPT = (
    "Eye Of Hermes (EOH), built on God's Eye View (live 3D globe of public aircraft, ships, satellites, hazards and weather) "
    "may be open in the user's Hermes pane. godseye_live_* tools act on that live globe: "
    "godseye_live_context answers 'what am I looking at' (camera, focus, nearby); "
    "godseye_live_analyst_query counts or lists what is loaded (e.g. flights in view); "
    "godseye_live_fly_to_location, _set_layer_visibility, _set_visual_style (surveillance = NVG/night vision, thermal, etc.)"
    ", _annotate_map and _move_camera change it. Each turn may include one EOH map line "
    "(pane connected or busy, camera center, range, pitch, heading, layers, pin count, open drawer) "
    "and [EOH watch alert] lines when a watch fires; "
    "trust the map line instead of calling godseye_live_get_current_view_state only to orient. "
    "Large tools default to detail=concise (ids and counts, no screenshots); pass detail=full for full objects or images. "
    "Failures return code, message and fix: do what fix says. "
    "If a live tool reports no pane, tell the user to open the Eye Of Hermes (EOH) pane; the godseye_* data tools work without it. "
    "godseye_brief_and_show briefs a place and shows it in one call; godseye_doctor diagnoses setup. " + policy.POLICY)


def load_live_catalog() -> list[dict]:
    return json.loads(LIVE_SNAPSHOT.read_text())


FANOUT_PROP = {"type": "boolean", "description": "Send to EVERY open EOH pane instead of only the "
               "pane next to this chat (default false; only when the user asks for all panes)."}


def live_schema(params: dict | None) -> dict:
    schema = json.loads(json.dumps(params or {"type": "object", "properties": {}}))
    schema.setdefault("properties", {})["fanout"] = FANOUT_PROP
    return schema


FRAMING_PROP = {"type": "string", "enum": ["street", "rooftop", "district"],
                "description": "Preset camera framing applied after arrival: street = 150 m range, pitch -15 "
                               "(standing across the street); rooftop = 450 m, -30; district = 2500 m, -45. "
                               "rangeM overrides the range (minimum 100 m). The result's camera has pitchDeg/headingDeg."}


DRAWER_PROPS = {
    "description_md": {"type": "string", "maxLength": 4000,
                       "description": "Pin drawer body (click the pin or godseye_live_pin_drawer). Markdown: "
                                      "**bold**, *italic*, `code`, # headings, - lists, [text](https://...) links. "
                                      "Raw HTML is shown as text."},
    "images": {"type": "array", "maxItems": 6, "description": "Pin drawer images (http/https URLs only, lazy-loaded).",
               "items": {"type": "object", "properties": {"url": {"type": "string"}, "alt": {"type": "string"},
                                                          "caption": {"type": "string"}}, "required": ["url"]}},
    "links": {"type": "array", "maxItems": 12, "description": "Pin drawer links (http/https only; open in a new tab).",
              "items": {"type": "object", "properties": {"url": {"type": "string"}, "label": {"type": "string"}},
                        "required": ["url"]}},
}


TAGS_PROP = {"type": "object", "description": "Pin facets for the pane's filter chips / godseye_live_filter_pins "
             "(godseye_find_places fills these from OSM): amenity, shop, tourism, leisure, cuisine, category, "
             "rating or stars, opening_hours (OSM syntax).", "additionalProperties": {"type": ["string", "number"]}}

AREA_PROP = {"type": "object", "description": "Exactly one of: bbox [south, west, north, east]; polygon [[lat, lon], ...]; "
             "lat + lon + radius_km; or view=true (the open pane's current view, resolved once at create time).",
             "properties": {"bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                            "polygon": {"type": "array", "minItems": 3, "maxItems": 200,
                                        "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}},
                            "lat": {"type": "number", "minimum": -90, "maximum": 90},
                            "lon": {"type": "number", "minimum": -180, "maximum": 180},
                            "radius_km": {"type": "number", "minimum": 0.1, "maximum": 2000},
                            "view": {"type": "boolean"}}}


def extend_live_schema(name: str, schema: dict) -> dict:
    """Hermes-side features the pane shim adds on top of upstream tools."""
    props = schema.setdefault("properties", {})
    if name == "fly_to_location":
        props["framing"] = FRAMING_PROP
    if name == "annotate_map":
        props["flyTo"] = {"anyOf": [{"type": "boolean"}, {"type": "string", "enum": ["all"]}],
                          "description": "true frames the first mark; 'all' frames every mark drawn in this call."}
        props["intent"] = {"type": "string", "maxLength": 240,
                           "description": "REQUIRED whenever you create pins: the user's request in their words "
                                          "(e.g. 'ramen near me', 'FIRMS fires last 24h'). Shapes which facts go "
                                          "in each pin's drawer. Always also fill description_md / images / links "
                                          "yourself when you know them."}
        props["context"] = {"type": "string", "maxLength": 240,
                            "description": "Alias of intent (user request / investigation context)."}
        items = (props.get("annotations") or {}).get("items")
        if isinstance(items, dict):
            ip = items.setdefault("properties", {})
            t = ip.get("type")
            if isinstance(t, dict) and isinstance(t.get("enum"), list) and "card" not in t["enum"]:
                t["enum"] = t["enum"] + ["card"]
            ip["label"] = {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}, "maxItems": 3}],
                           "description": "Mark label. Use a newline-separated string or a list for 2-3 lines."}
            ip["title"] = {"type": "string", "description": "card: heading line; pin: drawer title (default: label)"}
            ip.update(DRAWER_PROPS)
            ip["tags"] = TAGS_PROP
            ip["rows"] = {"type": "array", "maxItems": 8, "description": "card: rows like a menu or price list",
                          "items": {"type": "object", "properties": {"text": {"type": "string"},
                                                                     "price": {"type": ["string", "number"]}},
                                    "required": ["text"]}}
            t2 = ip.get("type")
            if isinstance(t2, dict):
                t2["description"] = (t2.get("description") or "") + (" 'card' = a callout box anchored to a point "
                                     "(target or latitude/longitude) with title and rows [{text, price}].")
    return schema


def make_live_handler(name: str):
    def handler(args: dict, **kw) -> str:
        from . import composer
        args = dict(args or {})
        mode = detail.pop_detail(args) if name in detail.LARGE_LIVE else "full"
        fanout = bool(args.pop("fanout", False))
        reason = policy.refuse_reason(args)
        if reason:
            return _present_error({"ok": False, "error": reason}, name)
        if name == "annotate_map":
            from . import enrich
            intent = args.get("intent") or args.get("context")
            args["annotations"] = enrich.enrich_annotations(args.get("annotations") or [], intent=intent, mode="light")
        out = composer.live_call(name, args, session_id=kw.get("session_id"), fanout=fanout)
        if name == "set_map_stack":
            out = composer.remediate_map_stack(out)
        if name in ("osm_query", "resolve_area"):
            from . import poi
            if name == "resolve_area":
                out = poi.resolve_area_fallback(args, out, lambda a: composer.live_call(
                    name, a, session_id=kw.get("session_id"), fanout=fanout))
            out = poi.remediate(name, out) if isinstance(out, dict) else out
        if not isinstance(out, dict):
            out = {"ok": False, "error": str(out)[:500]}
        out = errors.annotate(out, tool=name)
        if name in detail.LARGE_LIVE:
            return detail.render_obj(out, mode)
        text = json.dumps(out, ensure_ascii=False)
        return text if len(text) <= 20000 else text[:20000] + "…(truncated)"
    return handler


def register_tools(ctx) -> None:
    for tool in load_live_catalog():
        name = LIVE_PREFIX + tool["name"]
        desc = f"[Live globe in the open EOH pane] {tool.get('description', '')}\n\n{policy.POLICY}"
        params = extend_live_schema(tool["name"], live_schema(tool.get("parameters")))
        if tool["name"] in detail.LARGE_LIVE:
            params = detail.add_detail(params)
        ctx.register_tool(
            name=name, toolset=TOOLSET,
            schema={"name": name, "description": desc[:4000],
                    "parameters": params},
            handler=make_live_handler(tool["name"]), description=f"Live globe: {tool['name']}", emoji="🌐")
    try:
        ctx.register_system_prompt_section("godseye.live-globe", SYSTEM_PROMPT, max_chars=1500)
    except Exception:
        pass
    try:
        turn_state.register(ctx)
    except Exception:
        pass
    register_extras(ctx)

    for tool in load_catalog():
        name = f"godseye_{tool['name']}"
        desc = f"{tool.get('description', '')}\n\n{policy.POLICY}"
        params = tool.get("inputSchema") or {"type": "object", "properties": {}}
        if tool["name"] in detail.LARGE_DATA:
            params = detail.add_detail(params)
        ctx.register_tool(
            name=name, toolset=TOOLSET,
            schema={"name": name, "description": desc, "parameters": params},
            handler=make_handler(tool["name"]), check_fn=_available,
            description=tool.get("title", ""), emoji="🌍")

    def hud_handler(args: dict, **_kw) -> str:
        try:
            return json.dumps(hud.summarize(ctx.llm, (args or {}).get("context") or {}))
        except Exception as exc:
            return json.dumps({"error": f"HUD summary failed: {exc}"})

    ctx.register_tool(
        name="godseye_hud_summary", toolset=TOOLSET,
        schema={"name": "godseye_hud_summary",
                "description": "One-line God's Eye View HUD caption for a camera/layer context, "
                               "generated by the Hermes model (replaces the OpenAI HUD summary).",
                "parameters": {"type": "object", "properties": {"context": {
                    "type": "object", "description": "HUD context: locale, place, altitude, layer counts."}}}},
        handler=hud_handler, check_fn=_available, description="HUD summary via Hermes", emoji="🛰️")


SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "godseye-analyst"

EXTRA_TOOLS = {
    "godseye_brief_and_show": (
        "Brief a place AND show it on the live globe in one step: runs situation_brief (or "
        "military_awareness when the question is about military activity), flies the open EOH "
        "pane there, turns on the brief's layers, pins the top hits and sets a caption line. Returns the "
        "brief text plus what was shown; if no pane is open it still returns the brief and says so.",
        {"type": "object", "properties": {
            "place": {"type": "string", "description": "Place name, e.g. \"Tokyo\" or \"Strait of Hormuz\"."},
            "question": {"type": "string", "description": "The user's question (used to pick military_awareness)."},
            "lat": {"type": "number", "minimum": -90, "maximum": 90},
            "lon": {"type": "number", "minimum": -180, "maximum": 180},
            "radius_km": {"type": "number", "minimum": 1, "maximum": 2000},
            "top_n": {"type": "integer", "minimum": 0, "maximum": 24, "default": 5},
            "military": {"type": "boolean", "description": "Force military_awareness (true) or situation_brief (false)."},
            "intent": {"type": "string", "maxLength": 240,
                       "description": "User's request (default: question or place). Shapes which facts each "
                                      "pinned hit shows in its drawer."},
            "context": {"type": "string", "maxLength": 240, "description": "Alias of intent."},
            "pin_details": {"type": "array", "maxItems": 24, "description": "Optional drawer content per pinned hit, "
                            "in hit order (nearest first): {title, description_md, images[], links[]}. Defaults are "
                            "built from each hit's row.", "items": {"type": "object", "properties": {
                                "title": {"type": "string"}, **DRAWER_PROPS}}}}},
        "🛰️"),
    "godseye_doctor": (
        "Health check for Eye Of Hermes (EOH): Node 24, upstream checkout, app on :4173, upstream "
        "patches, live relay, connected panes, per-provider key presence (CESIUM_ION_TOKEN, "
        "GOOGLE_MAPS_API_KEY, MAPILLARY_CLIENT_TOKEN, AISSTREAM_API_KEY, FIRMS_MAP_KEY) in the env file "
        "and panel bundle (values never printed), active map stack, layer feed states from "
        "feedProvenance, and annotation count. Every failed check includes fix text. Read-only.",
        {"type": "object", "properties": {}}, "🩺"),
    "godseye_share_link": (
        "A God's Eye View share link for the current live view (camera, style, layers), on the user's "
        "public God's Eye View URL. Needs the pane open.",
        {"type": "object", "properties": {}}, "🔗"),
    "godseye_home": (
        "Saved home view of the live globe: action=go flies home, action=set saves the current view (or "
        "the given lat/lon/altitude_m/style/layers), action=get reads it. The pane also opens at home.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["go", "set", "get"], "default": "go"},
            "lat": {"type": "number", "minimum": -90, "maximum": 90},
            "lon": {"type": "number", "minimum": -180, "maximum": 180},
            "altitude_m": {"type": "number", "minimum": 100, "maximum": 40000000},
            "style": {"type": "string", "enum": ["normal", "retro", "surveillance", "thermal", "anime", "noir"]},
            "layers": {"type": "array", "items": {"type": "string"}}}},
        "🏠"),
    "godseye_live_list_annotations": (
        "List the annotations stored for this chat's scene (id, type, label, coordinates) and the saved camera. "
        "The scene is kept server-side and re-applied automatically when a pane is reopened or reloaded.",
        {"type": "object", "properties": {}}, "📍"),
    "godseye_live_delete_annotation": (
        "Delete one stored annotation by id (from godseye_live_list_annotations) and redraw the rest on the pane.",
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}, "🗑️"),
    "godseye_live_restore_scene": (
        "Re-apply this chat's stored scene (camera + every annotation, by stored coordinates, no geocoding) to the "
        "open pane. Normally automatic on pane (re)open; use it after a manual clear or to force a redraw.",
        {"type": "object", "properties": {}}, "♻️"),
    "godseye_live_reload_pane": (
        "Make the open pane re-fetch its panel and rebuild its frame (e.g. after a panel rebuild); the scene is "
        "restored automatically afterwards.",
        {"type": "object", "properties": {"fanout": FANOUT_PROP}}, "🔄"),
    "godseye_rebuild_panel": (
        "Rebuild God's Eye View's dist/panel bundle (npm run build:panel with ~/.config/gods-eye-view.env exported, "
        "Node 24) and reload every open pane. A current build reads dist/panel/config.json, rewritten when the pane "
        "is served, so a CESIUM_ION_TOKEN change does not need this. Older builds still bake the key. Takes ~20-60 s.",
        {"type": "object", "properties": {}}, "🛠️"),
    "godseye_live_run_tour": (
        "Run a guided tour on the live globe in the background and return a jobId at once. Each stop: caption, "
        "fly (waits for arrival), framing tilt (street = ~150 m range + tilt, 'standing across the street'; "
        "rooftop; district), optional continuous orbit, real wall-clock dwell. Ends stationary. Poll with "
        "godseye_live_tour_status; stop with godseye_live_stop_tour.",
        {"type": "object", "properties": {
            "stops": {"type": "array", "minItems": 1, "maxItems": 24, "items": {"type": "object", "properties": {
                "lat": {"type": "number", "minimum": -90, "maximum": 90},
                "lon": {"type": "number", "minimum": -180, "maximum": 180},
                "name": {"type": "string"}, "caption": {"type": "string", "maxLength": 160},
                "rangeM": {"type": "number", "minimum": 100}}}},
            "framing": {"type": "string", "enum": ["street", "rooftop", "district"], "default": "street"},
            "rangeM": {"type": "number", "minimum": 100, "maximum": 20000000,
                       "description": "Camera range per stop; default from framing (street 150, rooftop 450, district 2500). Minimum 100 m."},
            "orbit": {"type": ["string", "null"], "enum": ["slow", "normal", "fast", None], "default": None},
            "dwellMs": {"type": "integer", "minimum": 0, "maximum": 600000, "default": 20000},
            "connect": {"type": ["string", "null"], "enum": ["route", None], "default": None,
                        "description": "route = pin every stop and draw a route through them first."},
            "preview": {"type": ["string", "null"], "description": "Reserved. Use godseye_preview_swap to own a single desktop preview tab (desktop_preview is a Hermes core tool with no native swap)."},
            "intent": {"type": "string", "maxLength": 240,
                       "description": "User's request for this tour; used to fill each stop pin's drawer."},
            "context": {"type": "string", "maxLength": 240, "description": "Alias of intent."}},
         "required": ["stops"]}, "🧭"),
    "godseye_live_pin_drawer": (
        "Open or close the info drawer of a pin on the live globe (the same drawer a click opens). It shows the "
        "pin's title, description_md, images and links (set them in annotate_map). Pick the pin by id (annotate_map "
        "item id), label, or latitude/longitude (nearest pin). The open drawer is restored with the scene.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["open", "close", "state"], "default": "open"},
            "id": {"type": "string", "description": "Pane annotation id from annotate_map items[].id, e.g. anno-3."},
            "label": {"type": "string"},
            "latitude": {"type": "number", "minimum": -90, "maximum": 90},
            "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            "flyTo": {"type": "boolean", "default": False, "description": "Also fly the camera to the pin."},
            "fanout": FANOUT_PROP}}, "🗂️"),
    "godseye_live_filter_pins": (
        "Filter and cluster the pins on the live globe (the same chips the pane shows). categories match pin "
        "tags (amenity / shop / tourism / cuisine / category, e.g. ramen, cafe, bar); min_rating hides pins rated "
        "below it; open_now hides pins whose OSM opening_hours say closed now (unknown hours stay visible unless "
        "include_unknown=false). cluster=false turns count bubbles off. Filters persist in the scene. "
        "action=state reads filters, counts, clusters and per-pin open state; action=expand zooms into a cluster.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["set", "state", "expand"], "default": "set"},
            "categories": {"type": "array", "items": {"type": "string"}, "maxItems": 16,
                           "description": "Keep pins with ANY of these categories; [] = all."},
            "min_rating": {"type": ["number", "null"], "minimum": 0, "maximum": 10},
            "open_now": {"type": "boolean"},
            "include_unknown": {"type": "boolean", "default": True,
                                "description": "Keep pins with unknown rating/hours when filtering by them."},
            "cluster": {"type": "boolean", "default": True},
            "clear": {"type": "boolean", "description": "Reset every filter."},
            "cluster_id": {"type": "string", "description": "action=expand: id from action=state clusters[]."},
            "fanout": FANOUT_PROP}}, "🔎"),
    "godseye_watch_create": (
        "Watch an area and alert the user on NEW items: feeds earthquakes (USGS, keyless), fires (NASA FIRMS, needs "
        "FIRMS_MAP_KEY), flights (OpenSky, keyless, rate-limited), ships (needs AISSTREAM_API_KEY and the app), pois "
        "(OSM, needs conditions.pois.category/cuisine). Only create when the user asks. The first run records what "
        "is already there (baseline) without alerting; later runs alert on unseen ids only, pin each new item with "
        "drawer details and queue the alert for the next chat turn (deliver=inject also starts a turn when Hermes "
        "allows plugin injection). Feeds without their key report KEY_MISSING.",
        {"type": "object", "properties": {
            "name": {"type": "string", "maxLength": 80},
            "area": AREA_PROP,
            "feeds": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": ["earthquakes", "fires", "flights", "ships", "pois"]}},
            "conditions": {"type": "object", "description": "Per feed: earthquakes {min_magnitude (default 2.5), lookback_days}; "
                           "fires {min_frp_mw, confidence[], source}; flights {callsign_prefix, min_altitude_m, max_altitude_m, "
                           "airborne_only}; ships {type}; pois {category, cuisine}."},
            "interval_s": {"type": "integer", "minimum": 60, "maximum": 604800, "default": 900},
            "baseline": {"type": "boolean", "default": True, "description": "false = alert on what is already there on the first run."},
            "pin": {"type": "boolean", "default": True},
            "deliver": {"type": "string", "enum": ["queue", "inject"], "default": "queue"},
            "run_now": {"type": "boolean", "default": True, "description": "Run the first check right away."}},
         "required": ["area", "feeds"]}, "👁️"),
    "godseye_watch_list": (
        "List area watches (area, feeds, interval, active, last run, per-feed status incl. KEY_MISSING, seen counts) "
        "and the number of queued alerts. check=<id> runs that watch now and returns what is new.",
        {"type": "object", "properties": {"check": {"type": "string", "description": "Watch id to check now."}}}, "👁️"),
    "godseye_watch_pause": (
        "Pause (active=false) or resume (active=true) an area watch by id.",
        {"type": "object", "properties": {"id": {"type": "string"}, "active": {"type": "boolean", "default": False}},
         "required": ["id"]}, "⏸️"),
    "godseye_watch_delete": (
        "Delete an area watch and its last-seen state. Needs confirm=true; ask the user first.",
        {"type": "object", "properties": {"id": {"type": "string"}, "confirm": {"type": "boolean", "default": False}},
         "required": ["id"]}, "🗑️"),
    "godseye_live_tour_status": (
        "Status of a tour job: state, index, current stop, remaining, errors.",
        {"type": "object", "properties": {"jobId": {"type": "string"}}, "required": ["jobId"]}, "🧭"),
    "godseye_live_stop_tour": (
        "Cancel a running tour (the given jobId, else the latest) and stop camera motion.",
        {"type": "object", "properties": {"jobId": {"type": "string"}}}, "⏹️"),
    "godseye_find_places": (
        "Find real places (POIs) without any Overpass config: category/cuisine search in a box via public "
        "Overpass mirrors, or name/address lookup via Nominatim. e.g. query=\"ramen restaurants in Montreal\". "
        "Returns name, lat/lon, address, website, opening hours. pin=true pins them on the live globe.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "e.g. \"ramen restaurants in Montreal\" or a name/address."},
            "category": {"type": "string", "description": "restaurant, cafe, bar, hotel, museum, pharmacy, ..."},
            "cuisine": {"type": "string"},
            "near": {"description": "Place name or {lat, lon}.", "type": ["string", "object"]},
            "lat": {"type": "number", "minimum": -90, "maximum": 90},
            "lon": {"type": "number", "minimum": -180, "maximum": 180},
            "radiusM": {"type": "number", "minimum": 50, "maximum": 15000},
            "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                     "description": "[south, west, north, east]"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            "pin": {"type": "boolean", "default": False, "description": "Pin results (max 24) via annotate_map."},
            "intent": {"type": "string", "maxLength": 240,
                       "description": "User's request (default: the query). Shapes pin drawer facts "
                                      "(e.g. cuisine/hours/menu for food). Always pass this when pinning."},
            "context": {"type": "string", "maxLength": 240, "description": "Alias of intent."}}},
        "🍜"),
    "godseye_poi_details": (
        "Best-effort details for one venue: OSM record, website, menu URL if found, and verbatim price "
        "snippets scraped from its site. Never invents prices; empty when none are scrapeable.",
        {"type": "object", "properties": {"name": {"type": "string"}, "city": {"type": "string"}},
         "required": ["name"]}, "🧾"),
    "godseye_live_panes": (
        "List Eye Of Hermes (EOH) panes (id, optional label, bound chat sessions, last-poll age, busy). "
        "Stale closed records are pruned automatically; pass prune=true to force a prune now. "
        "Pass label (and optional pane_id) to name the pane bound to this chat. One pane per "
        "session by default; pane ids stay stable across remounts.",
        {"type": "object", "properties": {
            "prune": {"type": "boolean", "description": "Drop stale closed pane records now."},
            "label": {"type": "string", "description": "Optional label for this chat's pane (shown in the list)."},
            "pane_id": {"type": "string", "description": "Pane id to label; default = this session's bound pane."}}}, "🪟"),
    "godseye_live_context": (
        "What the user is looking at in the EOH pane: camera (center, heading, pitch, range, position), visible bbox, "
        "the selected pin (drawer open), the last clicked ground point, shapes the user drew (box/polygon/line with "
        "distance or area), and focus = the best guess for 'this/here' (open pin > newest drawing > last click > view "
        "centre). By default also answers 'what is this': Nominatim reverse geocode of the focus (1 request per 1.2 s) "
        "plus nearby named places/buildings (Overpass). Call it FIRST whenever the user says this, here, around here, "
        "that building, what am I looking at.",
        {"type": "object", "properties": {
            "describe": {"type": "boolean", "default": True, "description": "Reverse geocode + nearby places for the focus."},
            "nearby": {"type": "boolean", "default": True},
            "category": {"type": "string", "description": "Nearby filter, e.g. restaurant, cafe, hotel (default: any named place)."},
            "radius_m": {"type": "number", "minimum": 30, "maximum": 2000, "description": "Nearby radius (default from zoom)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8}}}, "👁️"),
    "godseye_what_is_here": (
        "Reverse geocode a coordinate (Nominatim: name, address, OSM type, website/wikipedia tags) and list nearby named "
        "places/buildings, nearest first. Works without a pane.",
        {"type": "object", "properties": {
            "lat": {"type": "number", "minimum": -90, "maximum": 90}, "lon": {"type": "number", "minimum": -180, "maximum": 180},
            "radius_m": {"type": "number", "minimum": 30, "maximum": 2000, "default": 150},
            "category": {"type": "string"}, "nearby": {"type": "boolean", "default": True},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8}}, "required": ["lat", "lon"]}, "📌"),
    "godseye_measure": (
        "Measure on the globe. op=distance: great-circle distance through 2+ points (total + per leg + bearing); "
        "op=area: area + perimeter of a polygon (3+ points); op=route: travel distance/time by walk/bike/drive via the "
        "public OSRM server (clear routing_unavailable error when it is down). Points can be coordinates, pins on the pane "
        "(pins: ids or labels) or a shape the user drew (use_drawn: 'latest' or a shape id; a box/polygon defaults to "
        "area). draw=true draws the line/polygon with its label on the pane.",
        {"type": "object", "properties": {
            "op": {"type": "string", "enum": ["distance", "area", "route"]},
            "points": {"type": "array", "items": {"type": "object", "properties": {"lat": {"type": "number", "minimum": -90, "maximum": 90}, "lon": {"type": "number", "minimum": -180, "maximum": 180}}, "required": ["lat", "lon"]}, "maxItems": 200},
            "pins": {"type": "array", "items": {"type": "string"}, "description": "Pin ids (anno-3) or labels, in order."},
            "use_drawn": {"type": "string", "description": "'latest' or a drawn shape id from godseye_live_context."},
            "mode": {"type": "string", "enum": ["walk", "bike", "drive"], "default": "walk", "description": "op=route only."},
            "draw": {"type": "boolean", "default": True}, "label": {"type": "string"}, "color": {"type": "string"}}}, "📏"),
    "godseye_isochrone": (
        "Reach within N minutes (walk/bike/drive) from a point: isochrone polygons from the public Valhalla server, "
        "else an approximate reach polygon from one OSRM table call (marked approximate), else a clear "
        "routing_unavailable error (never a fake straight-line circle). Without lat/lon it uses the pane focus "
        "(selected pin / last click / view centre). Draws the rings with labels on the pane.",
        {"type": "object", "properties": {
            "lat": {"type": "number", "minimum": -90, "maximum": 90}, "lon": {"type": "number", "minimum": -180, "maximum": 180},
            "minutes": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 120}, "maxItems": 4, "default": [10]},
            "mode": {"type": "string", "enum": ["walk", "bike", "drive"], "default": "walk"},
            "draw": {"type": "boolean", "default": True},
            "include_polygon": {"type": "boolean", "default": False, "description": "Return polygon coordinates too."}}}, "⏱️"),
    "godseye_live_draw": (
        "Shapes on the live globe. action=start kind=box|polygon|line puts the pane in draw mode for the USER (box: 2 "
        "clicks; polygon/line: clicks, double-click or Enter finishes, Esc cancels); action=add draws Hermes' own "
        "line/polygon/box from points with a label; list / remove (id) / clear (who=all|user|hermes) / cancel. "
        "Hermes-drawn shapes are restored with the scene.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["start", "add", "list", "remove", "clear", "cancel"], "default": "add"},
            "kind": {"type": "string", "enum": ["box", "polygon", "line"]},
            "points": {"type": "array", "items": {"type": "object", "properties": {"lat": {"type": "number", "minimum": -90, "maximum": 90}, "lon": {"type": "number", "minimum": -180, "maximum": 180}}, "required": ["lat", "lon"]}, "maxItems": 600},
            "id": {"type": "string"}, "label": {"type": "string", "maxLength": 80}, "color": {"type": "string"},
            "who": {"type": "string", "enum": ["all", "user", "hermes"], "default": "all"}}}, "✏️"),
    "godseye_live_compare": (
        "Side-by-side swipe on the live globe between two imagery layers with a draggable divider. Sides: 'current' "
        "(what the globe shows now), 'osm', or 'wayback:YYYY[-MM[-DD]]' = Esri World Imagery Wayback historical "
        "imagery release on/before that date (keyless). God's Eye View map stacks cannot be split (one at a time). "
        "action=list_dates lists Wayback release dates; position (0-1) / toggle (before/after flip) / state / stop.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["start", "stop", "position", "toggle", "state", "list_dates"], "default": "start"},
            "left": {"type": "string", "default": "wayback:2014", "description": "Left side (before)."},
            "right": {"type": "string", "default": "current", "description": "Right side (after)."},
            "position": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5}}}, "🪞"),
    "godseye_preview_swap": (
        "Open a URL in the Hermes desktop preview pane, closing the previous URL this plugin opened "
        "(single-tab swap). desktop_preview is a Hermes core tool and cannot gain a native swap action; "
        "this helper tracks the last URL and closes it first. When the desktop emitter is unavailable it "
        "returns desktop_preview_steps for the agent to run.",
        {"type": "object", "properties": {
            "url": {"type": "string", "description": "URL or file path to show."},
            "label": {"type": "string", "description": "Optional preview tab label."},
            "action": {"type": "string", "enum": ["swap", "close"], "default": "swap",
                       "description": "swap = close previous + open url; close = dismiss the tracked tab only."}}}, "🖼️"),

    "godseye_research_pins": (
        "Research pins in the current scene and write a short summary, photos and sources into each pin's "
        "drawer (description_md with a Sources section, images[], links[]), then persist them. Free keyless "
        "sources only: Wikipedia/Wikidata REST, Wikimedia Commons, OSM tags already on the pin, website meta "
        "description. mode=light (fast, cached, local+tiny wiki) or full (default). Never invents facts — "
        "missing channels are marked unavailable. Cap: 24 pins, few HTTP calls each, disk cache. "
        "This plugin cannot call Hermes web_search; when sources are thin, run web_search yourself and pass "
        "verified snippets as agent_notes:[{id|label, summary, links[], images[]}].",
        {"type": "object", "properties": {
            "ids": {"type": "array", "items": {"type": "string"}, "maxItems": 24,
                    "description": "Scene annotation ids (a1…) or pane ids (anno-3). Default: all pins."},
            "labels": {"type": "array", "items": {"type": "string"}, "maxItems": 24},
            "all": {"type": "boolean", "default": True, "description": "Research every pin when ids/labels omitted."},
            "mode": {"type": "string", "enum": ["light", "full"], "default": "full"},
            "agent_notes": {"type": "array", "maxItems": 24, "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "label": {"type": "string"}, "summary": {"type": "string"},
                "images": {"type": "array", "items": {"type": "object"}},
                "links": {"type": "array", "items": {"type": "object"}}}}},
            "persist": {"type": "boolean", "default": True}}}, "🔎"),
    "godseye_investigation_save": (
        "Save the whole current scene (pins with drawer content, notes, routes, cards, camera/saved views) "
        "as a named investigation under plugin-data/godseye/investigations/.",
        {"type": "object", "properties": {
            "name": {"type": "string", "description": "Investigation name (letters, digits, space _ . -)."},
            "notes": {"type": "string", "maxLength": 4000},
            "views": {"type": "array", "maxItems": 12, "items": {"type": "object"},
                      "description": "Optional named camera views [{name, lat, lon, heightM, ...}]."}},
         "required": ["name"]}, "💾"),
    "godseye_investigation_list": (
        "List saved investigations (name, pin/route counts, saved_at, path).",
        {"type": "object", "properties": {}}, "📚"),
    "godseye_investigation_load": (
        "Load a named investigation into this chat's scene and restore it on the open pane.",
        {"type": "object", "properties": {
            "name": {"type": "string"},
            "restore": {"type": "boolean", "default": True}}, "required": ["name"]}, "📂"),
    "godseye_investigation_delete": (
        "Delete a named investigation. Requires confirm=true; refused without it.",
        {"type": "object", "properties": {
            "name": {"type": "string"},
            "confirm": {"type": "boolean", "description": "Must be true to delete."}},
         "required": ["name", "confirm"]}, "🗑️"),
    "godseye_investigation_report": (
        "Export a self-contained HTML + Markdown report for a named investigation (or the current scene): "
        "title, map screenshot when the pane can capture its canvas (otherwise a note), pin table, each pin's "
        "drawer, routes and sources. Saved under plugin-data/godseye/reports/; returns paths. Optionally "
        "include a share link for the current view.",
        {"type": "object", "properties": {
            "name": {"type": "string", "description": "Saved investigation name; omit to export the live scene."},
            "share": {"type": "boolean", "default": False, "description": "Also call godseye_share_link."},
            "capture": {"type": "boolean", "default": True, "description": "Try pane canvas screenshot."}}}, "📄"),
    "godseye_investigation_export": (
        "Export a saved investigation, or the current scene if name is omitted, as GeoJSON (RFC 7946, longitude then "
        "latitude) and KML. Pins are Points with their drawer and enrichment properties; drawings and areas are "
        "Polygons or LineStrings; routes are LineStrings. format=pack also writes a zip of the GeoJSON, the KML, the "
        "Markdown report and any saved screenshots. Files are written under plugin-data/godseye/exports/. Returns "
        "paths and a short summary. Google photoreal tile imagery is not embedded (those terms allow viewing only). "
        "Property values are the investigation's own data.",
        {"type": "object", "properties": {
            "name": {"type": "string", "description": "Saved investigation name. Omit to export the current scene."},
            "format": {"type": "string", "enum": ["geojson", "kml", "both", "pack"], "default": "both",
                       "description": "both writes GeoJSON and KML. pack adds a zip with the report and saved screenshots."}}},
        "📤"),
}


CONTEXT_TOOLS = {"godseye_live_context": "live_context", "godseye_what_is_here": None, "godseye_measure": "measure",
                 "godseye_isochrone": "isochrone", "godseye_live_draw": "draw", "godseye_live_compare": "compare"}


def extra_handler(name: str):
    def handler(args: dict, **kw) -> str:
        from . import composer, home
        args, sid = dict(args or {}), kw.get("session_id")
        mode = detail.pop_detail(args) if name in detail.LARGE_EXTRA else "full"
        try:
            if name == "godseye_brief_and_show":
                out = composer.brief_and_show(args, session_id=sid)
            elif name == "godseye_doctor":
                out = composer.doctor()
            elif name == "godseye_share_link":
                out = composer.share_link(session_id=sid)
            elif name == "godseye_find_places":
                from . import poi
                out = poi.find_places(args)
                if args.get("pin") and out.get("ok") and out["places"]:
                    intent = args.get("intent") or args.get("context") or args.get("query")
                    anns = poi.pin_annotations(out["places"], intent=intent)
                    out["pinned"] = composer.live_call(
                        "annotate_map", {"annotations": anns, "intent": intent, "persist": True}, sid)
            elif name == "godseye_poi_details":
                from . import poi
                out = poi.poi_details(args)
            elif name == "godseye_live_panes":
                out = composer.panes_op(prune=bool(args.get("prune")), label=args.get("label"),
                                       pane_id=args.get("pane_id"), session_id=sid)
            elif name == "godseye_preview_swap":
                from . import preview as preview_mod
                if (args.get("action") or "swap") == "close":
                    out = preview_mod.close_tracked()
                else:
                    out = preview_mod.swap(str(args.get("url") or ""), label=str(args.get("label") or ""))
            elif name in CONTEXT_TOOLS:
                from . import context, measure as measure_mod
                if name == "godseye_what_is_here":
                    out = measure_mod.what_is_here(float(args["lat"]), float(args["lon"]), radius_m=float(args.get("radius_m") or 150),
                                                   nearby=bool(args.get("nearby", True)), category=args.get("category"),
                                                   limit=int(args.get("limit") or 8))
                else:
                    out = getattr(context, CONTEXT_TOOLS[name])(args, session_id=sid)
            elif name == "godseye_live_list_annotations":
                out = composer.scene_op("list", sid)
            elif name == "godseye_live_delete_annotation":
                out = composer.scene_op("delete", sid, id=str(args.get("id") or ""))
            elif name == "godseye_live_restore_scene":
                out = composer.scene_op("restore", sid)
            elif name == "godseye_live_pin_drawer":
                fan = bool(args.pop("fanout", False))
                out = composer.live_call("hermes_pin_drawer", args, sid, fanout=fan)
            elif name == "godseye_live_filter_pins":
                fan = bool(args.pop("fanout", False))
                out = composer.live_call("hermes_pin_filter", args, sid, fanout=fan)
            elif name.startswith("godseye_watch_"):
                out = watch_tool(name, args, sid)
            elif name == "godseye_live_reload_pane":
                out = composer.live_call("hermes_reload", {}, sid, fanout=bool(args.get("fanout")))
            elif name == "godseye_rebuild_panel":
                out = composer.rebuild_panel(sid)
            elif name == "godseye_live_run_tour":
                out = composer.tour_op("start", sid, args=args)
            elif name == "godseye_live_tour_status":
                out = composer.tour_op("status", sid, jobId=args.get("jobId"))
            elif name == "godseye_live_stop_tour":
                out = composer.tour_op("cancel", sid, jobId=args.get("jobId"))
            elif name == "godseye_research_pins":
                out = composer.research_pins_op(args, session_id=sid)
            elif name == "godseye_investigation_save":
                out = composer.investigation_op("save", sid, args=args)
            elif name == "godseye_investigation_list":
                out = composer.investigation_op("list", sid, args=args)
            elif name == "godseye_investigation_load":
                out = composer.investigation_op("load", sid, args=args)
            elif name == "godseye_investigation_delete":
                out = composer.investigation_op("delete", sid, args=args)
            elif name == "godseye_investigation_report":
                out = composer.investigation_op("report", sid, args=args)
            elif name == "godseye_investigation_export":
                out = composer.investigation_op("export", sid, args=args)
            else:
                action = args.pop("action", "go")
                if action == "get":
                    out = {"ok": True, "home": home.get(), "default": home.DEFAULT}
                elif action == "set":
                    out = composer.set_home(args, session_id=sid)
                else:
                    out = composer.go_home(session_id=sid)
        except Exception as exc:
            out = {"ok": False, "error": f"{name} failed: {type(exc).__name__}: {str(exc)[:300]}"}
        if not isinstance(out, dict):
            out = {"ok": False, "error": str(out)[:500]}
        out = errors.annotate(out, tool=name)
        if name in detail.LARGE_EXTRA:
            return detail.render_obj(out, mode)
        text = json.dumps(out, ensure_ascii=False)
        return text if len(text) <= 20000 else text[:20000] + "…(truncated)"
    return handler


def watch_tool(name: str, args: dict, sid=None) -> dict:
    from . import composer, watch
    st = watch.store()
    if name == "godseye_watch_create":
        out = st.create(args, session_id=sid, view_fn=lambda: composer.live_call("get_current_view_state", {}, sid))
        if out.get("ok") and args.get("run_now", True) is not False:
            out["first_check"] = st.check(out["watch"]["id"])
        return out
    if name == "godseye_watch_list":
        return st.check(str(args["check"])) if args.get("check") else st.list(sid)
    if name == "godseye_watch_pause":
        return st.set_active(str(args.get("id") or ""), bool(args.get("active", False)))
    return st.delete(str(args.get("id") or ""), args.get("confirm") is True)


def bind_watch_delivery(ctx) -> dict:
    """Alerts: queued for the next turn (pre_llm_call), optional ctx.inject_message for deliver=inject."""
    from . import watch
    st = watch.store()
    done = {"hook": False, "inject": False}
    if hasattr(ctx, "inject_message"):
        def deliver(w, text):
            sk = w.get("session_id")
            return bool(sk) and bool(ctx.inject_message(text, role="user", session_key=sk))
        st.deliver = deliver
        done["inject"] = True
    # One callback carries the map line and queued alerts. Registering a second
    # pre_llm_call would replace the first on hosts that store a single hook.
    from . import turn_state
    done["hook"] = turn_state.ensure_hook(ctx)
    st.ensure_running()  # only starts when a persisted watch is active
    return done


def register_extras(ctx) -> dict:
    """Composer tools, /gev, the analyst skill and home-view state. Each host API is optional:
    a Hermes build without it simply skips that piece. Returns what was registered."""
    from . import composer, home
    done = {"tools": [], "command": False, "skill": False, "state": False}
    state = getattr(ctx, "state", None)
    if state is not None and hasattr(state, "get") and hasattr(state, "set"):
        home.bind(state)
        done["state"] = True
    for name, (desc, params, emoji) in EXTRA_TOOLS.items():
        needs_mcp = name == "godseye_brief_and_show"
        if name in detail.LARGE_EXTRA:
            params = detail.add_detail(params)
        ctx.register_tool(name=name, toolset=TOOLSET,
                          schema={"name": name, "description": f"{desc}\n\n{policy.POLICY}", "parameters": params},
                          handler=extra_handler(name), description=desc.split(":")[0][:80], emoji=emoji,
                          **({"check_fn": _available} if needs_mcp else {}))
        done["tools"].append(name)
    try:
        done["watch"] = bind_watch_delivery(ctx)
    except Exception:
        done["watch"] = None
    if hasattr(ctx, "register_command"):
        try:
            ctx.register_command("gev", composer.gev_command,
                                 description="Eye Of Hermes (EOH): fly, layers, status, brief, home, share, doctor, research, save, list, load, delete, report, export",
                                 args_hint="<fly|layers|status|brief|home|share|doctor|research|save|list|load|delete|report|export> [args]")
            done["command"] = True
        except Exception:
            pass
    if hasattr(ctx, "register_skill") and (SKILL_DIR / "SKILL.md").is_file():
        try:
            ctx.register_skill("godseye-analyst", SKILL_DIR / "SKILL.md",
                               description="Eye Of Hermes (EOH) analyst playbook: data vs live tools, pane guidance, policy, examples.")
            done["skill"] = True
        except Exception:
            pass
    return done
