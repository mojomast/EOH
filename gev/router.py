"""Op router for the composable Eye Of Hermes tools.

Phase A registers ``godseye_view`` and ``godseye_query``. Every legacy name
still registered as an alias calls ``invoke``, which calls the same handler
the alias used before (``make_live_handler``, ``make_handler``, or
``extra_handler``). Tools whose composable home is not registered yet stay
as they are: their descriptions are not marked deprecated.

``CATALOG`` is the full mapping. ``docs/COMPOSABLE-TOOLS.md`` mirrors it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import policy

_HERE = {
    "me", "here", "nearby", "near me", "around here", "around me", "my location",
    "this", "this area", "the area", "this view", "current view",
}
_SPLIT = re.compile(r"\s+(?:in|near|around|at)\s+", re.I)
_STYLES = {"normal", "retro", "surveillance", "thermal", "anime", "noir"}
_STYLE_ALIASES = {
    "nvg": "surveillance",
    "night": "surveillance",
    "night vision": "surveillance",
    "night-vision": "surveillance",
    "nightvision": "surveillance",
    "flir": "thermal",
    "ir": "thermal",
    "infrared": "thermal",
}


@dataclass(frozen=True)
class Target:
    legacy: str
    composable: str
    op: str
    kind: str  # live, mcp, extra, hud
    phase: str  # A (routed now) or later
    note: str = ""

    @property
    def bare(self) -> str:
        if self.kind == "live":
            return self.legacy[len("godseye_live_"):]
        if self.kind == "mcp":
            return self.legacy[len("godseye_"):]
        return self.legacy


def _t(legacy: str, composable: str, op: str, kind: str, phase: str, note: str = "") -> Target:
    return Target(legacy, composable, op, kind, phase, note)


# Full tool map. Phase A rows are the ones godseye_view / godseye_query dispatch.
CATALOG: tuple[Target, ...] = (
    # --- godseye_view: camera, home, share, panes ---
    _t("godseye_live_fly_to_location", "godseye_view", "fly", "live", "A",
       "framing is an argument on fly (street, rooftop, district), not its own op"),
    _t("godseye_live_move_camera", "godseye_view", "move", "live", "A",
       "orbit, pan, tilt, rotate, rotate_globe, or stop"),
    _t("godseye_live_adjust_camera_zoom", "godseye_view", "zoom", "live", "A",
       "relative step only; whole-Earth is op=globe"),
    _t("godseye_live_zoom_to_globe", "godseye_view", "globe", "live", "A",
       "separate op because a relative zoom cannot reach the globe"),
    _t("godseye_home", "godseye_view", "home", "extra", "A",
       "action stays go, set, or get"),
    _t("godseye_share_link", "godseye_view", "share", "extra", "A",
       "share link for the open view"),
    _t("godseye_live_panes", "godseye_view", "panes", "extra", "A",
       "list, label, or prune pane records"),
    _t("godseye_show_in_gods_eye_view", "godseye_view", "show", "mcp", "later",
       "composed scene (area, layers, style, marks, follow); left off phase A so godseye_view stays a camera tool"),
    # --- godseye_layers ---
    _t("godseye_live_set_layer_visibility", "godseye_layers", "visibility", "live", "later",
       "one layer on or off"),
    _t("godseye_live_show_data_layers_menu", "godseye_layers", "menu", "live", "later",
       "open the layers menu"),
    _t("godseye_live_set_panel_open", "godseye_layers", "panel", "live", "later",
       "side panels are chrome, grouped with the menus"),
    _t("godseye_live_set_context_mode", "godseye_layers", "context_mode", "live", "later",
       "Contacts or Space Missions mode, not a data query"),
    _t("godseye_live_set_visual_style", "godseye_layers", "style", "live", "later",
       "home of style; phase A fly accepts style and calls this handler, so this name is not deprecated yet"),
    _t("godseye_live_set_cyber_sonar", "godseye_layers", "sonar", "live", "later",
       "HUD decoration, not a feed"),
    _t("godseye_live_set_detection", "godseye_layers", "detection", "live", "later",
       "detection overlay density"),
    _t("godseye_live_set_map_stack", "godseye_layers", "stack", "live", "later",
       "basemap stack"),
    _t("godseye_live_set_post_processing", "godseye_layers", "post", "live", "later",
       "post-processing toggles"),
    # --- godseye_query ---
    _t("godseye_find_places", "godseye_query", "find_places", "extra", "A",
       "keyless POI search; here=true or 'near me' uses the pane camera"),
    _t("godseye_live_osm_query", "godseye_query", "osm", "live", "A",
       "in-pane OSM places; defaults to the current view"),
    _t("godseye_live_context", "godseye_query", "context", "extra", "A",
       "what the pane is looking at, including reverse geocode"),
    _t("godseye_what_is_here", "godseye_query", "what_is_here", "extra", "A",
       "reverse geocode a coordinate; no pane unless here=true"),
    _t("godseye_situation_brief", "godseye_query", "brief", "mcp", "A",
       "situation_brief; place, area, or here=true"),
    _t("godseye_brief_and_show", "godseye_query", "brief_and_show", "extra", "A",
       "brief plus fly, layers, pins, and caption"),
    _t("godseye_live_analyst_query", "godseye_query", "analyst", "live", "A",
       "counts and lists over enabled layers"),
    _t("godseye_military_awareness", "godseye_query", "military", "mcp", "A",
       "military contacts around a place"),
    _t("godseye_poi_details", "godseye_query", "poi", "extra", "A",
       "one venue after find_places; not a feed"),
    _t("godseye_live_resolve_area", "godseye_query", "resolve_area", "live", "A",
       "areaId for analyst and osm; omitted from the research table"),
    _t("godseye_live_get_entity_context", "godseye_query", "entity", "live", "A",
       "selected or in-view records; the research query role named entity"),
    _t("godseye_get_terrain_height", "godseye_query", "terrain", "mcp", "later",
       "point elevation, not a streaming feed; not in the phase A query set"),
    _t("godseye_get_map_features", "godseye_query", "features", "mcp", "later",
       "named OSM features at a point, sibling of what_is_here; phase B"),
    # --- godseye_feeds ---
    _t("godseye_get_earthquakes", "godseye_feeds", "earthquakes", "mcp", "later", "USGS events"),
    _t("godseye_get_active_fires", "godseye_feeds", "fires", "mcp", "later", "NASA FIRMS hotspots"),
    _t("godseye_get_recent_launches", "godseye_feeds", "launches", "mcp", "later", "recent rocket launches"),
    _t("godseye_aircraft_in_area", "godseye_feeds", "aircraft_in_area", "mcp", "later", "aircraft in an area"),
    _t("godseye_find_aircraft", "godseye_feeds", "find_aircraft", "mcp", "later", "aircraft lookup"),
    _t("godseye_get_aircraft_track", "godseye_feeds", "aircraft_track", "mcp", "later", "one aircraft track"),
    _t("godseye_get_aircraft_info", "godseye_feeds", "aircraft_info", "mcp", "later", "one aircraft record"),
    _t("godseye_vessels_in_area", "godseye_feeds", "vessels_in_area", "mcp", "later", "ships in an area"),
    _t("godseye_find_vessel", "godseye_feeds", "find_vessel", "mcp", "later", "ship lookup"),
    _t("godseye_get_vessel_track", "godseye_feeds", "vessel_track", "mcp", "later", "one ship track"),
    _t("godseye_next_satellite_pass", "godseye_feeds", "next_pass", "mcp", "later",
       "MCP pass prediction; no pane. The live tool is op=satellite_pass"),
    _t("godseye_satellites_overhead", "godseye_feeds", "overhead", "mcp", "later", "satellites above a point"),
    _t("godseye_find_cctv_cameras", "godseye_feeds", "find_cctv", "mcp", "later", "public CCTV camera search"),
    _t("godseye_get_cctv_snapshot", "godseye_feeds", "cctv_snapshot", "mcp", "later", "one camera snapshot"),
    _t("godseye_find_alpr_cameras", "godseye_feeds", "alpr", "mcp", "later", "ALPR camera search"),
    _t("godseye_get_traffic_flow", "godseye_feeds", "traffic", "mcp", "later", "traffic flow"),
    _t("godseye_get_weather_map", "godseye_feeds", "weather", "mcp", "later", "weather raster"),
    _t("godseye_get_recent_imagery", "godseye_feeds", "imagery", "mcp", "later",
       "MCP recent imagery. The pane tool is op=imagery_live"),
    _t("godseye_find_submarine_cables", "godseye_feeds", "cables", "mcp", "later", "submarine cables"),
    _t("godseye_find_infrastructure", "godseye_feeds", "infrastructure", "mcp", "later", "mapped infrastructure"),
    _t("godseye_get_bhote_koshi_flood", "godseye_feeds", "flood", "mcp", "later", "single flood event, not a general query"),
    _t("godseye_get_cyclones", "godseye_feeds", "cyclones", "mcp", "later", "tropical cyclones"),
    _t("godseye_get_fire_perimeters", "godseye_feeds", "fire_perimeters", "mcp", "later", "wildfire perimeters"),
    _t("godseye_find_military_installations", "godseye_feeds", "military_sites", "mcp", "later",
       "OSM installation catalog; the brief is godseye_query op=military"),
    _t("godseye_live_find_imagery", "godseye_feeds", "imagery_live", "live", "later", "pane recent-imagery search"),
    _t("godseye_live_next_iss_pass", "godseye_feeds", "iss_pass", "live", "later", "next ISS pass from the pane"),
    _t("godseye_live_next_satellite_pass", "godseye_feeds", "satellite_pass", "live", "later",
       "next pass from the loaded pane catalog"),
    _t("godseye_live_control_cctv", "godseye_feeds", "cctv_control", "live", "later",
       "pane CCTV controls; snapshots stay op=cctv_snapshot"),
    _t("godseye_live_control_radio", "godseye_feeds", "radio", "live", "later", "internet radio playback"),
    # --- godseye_annotate ---
    _t("godseye_live_annotate_map", "godseye_annotate", "annotate", "live", "later", "pins, routes, cards"),
    _t("godseye_live_clear_annotations", "godseye_annotate", "clear", "live", "later", "clear marks on the pane"),
    _t("godseye_live_draw", "godseye_annotate", "draw", "extra", "later", "user draw mode or Hermes shapes"),
    _t("godseye_live_list_annotations", "godseye_annotate", "list", "extra", "later", "stored marks for this chat"),
    _t("godseye_live_delete_annotation", "godseye_annotate", "delete", "extra", "later", "delete one stored mark"),
    _t("godseye_live_filter_pins", "godseye_annotate", "filter", "extra", "later", "category, rating, open-now chips"),
    _t("godseye_live_pin_drawer", "godseye_annotate", "drawer", "extra", "later", "open or close one pin drawer"),
    _t("godseye_research_pins", "godseye_annotate", "research", "extra", "later", "fill drawers from free sources"),
    # --- godseye_nav ---
    _t("godseye_measure", "godseye_nav", "measure", "extra", "later",
       "inner distance, area, or route moves to argument measure when this tool exists, so it does not collide with op"),
    _t("godseye_isochrone", "godseye_nav", "isochrone", "extra", "later", "minutes of reach"),
    _t("godseye_live_fly_route", "godseye_nav", "fly_route", "live", "later",
       "dolly along an existing route; not a camera fly"),
    _t("godseye_live_compare", "godseye_nav", "compare", "extra", "later", "Wayback or imagery swipe"),
    # --- godseye_track ---
    _t("godseye_live_track_entity", "godseye_track", "track", "live", "later", "follow one entity"),
    _t("godseye_live_stop_tracking", "godseye_track", "stop", "live", "later", "stop following"),
    _t("godseye_live_select_nearest_aircraft", "godseye_track", "select_nearest", "live", "later",
       "fly, enable flights, select the nearest airborne aircraft"),
    _t("godseye_live_frame_overhead", "godseye_track", "frame", "live", "later",
       "frame nearby aircraft, ships, or satellites; not the street/rooftop/district framing argument"),
    _t("godseye_live_control_cockpit", "godseye_track", "cockpit", "live", "later",
       "ride a selected aircraft; a track action, not a layer"),
    # --- godseye_scene ---
    _t("godseye_investigation_save", "godseye_scene", "save", "extra", "later", "save the current scene"),
    _t("godseye_investigation_list", "godseye_scene", "list", "extra", "later", "list saved investigations"),
    _t("godseye_investigation_load", "godseye_scene", "load", "extra", "later", "load an investigation"),
    _t("godseye_investigation_delete", "godseye_scene", "delete", "extra", "later", "delete requires confirm=true"),
    _t("godseye_investigation_report", "godseye_scene", "report", "extra", "later", "HTML and Markdown report"),
    _t("godseye_investigation_export", "godseye_scene", "export", "extra", "later",
       "GeoJSON and KML, optional pack; not folded into view or query"),
    _t("godseye_live_restore_scene", "godseye_scene", "restore", "extra", "later", "re-apply this chat's scene"),
    _t("godseye_live_run_tour", "godseye_scene", "tour", "extra", "later", "start a guided tour"),
    _t("godseye_live_tour_status", "godseye_scene", "tour_status", "extra", "later", "poll a tour job"),
    _t("godseye_live_stop_tour", "godseye_scene", "tour_stop", "extra", "later", "cancel a tour"),
    _t("godseye_live_control_scene", "godseye_scene", "playback", "live", "later",
       "cinematic scene playback; not an investigation"),
    # --- godseye_watch ---
    _t("godseye_watch_create", "godseye_watch", "create", "extra", "later", "only when the user asks"),
    _t("godseye_watch_list", "godseye_watch", "list", "extra", "later",
       "check=<id> runs one watch now; there is no separate check tool"),
    _t("godseye_watch_pause", "godseye_watch", "pause", "extra", "later", "pause or resume"),
    _t("godseye_watch_delete", "godseye_watch", "delete", "extra", "later", "requires confirm=true"),
    # --- godseye_capture ---
    _t("godseye_live_set_hud", "godseye_capture", "hud", "live", "later", "HUD visibility and layout"),
    _t("godseye_hud_summary", "godseye_capture", "summary", "hud", "later",
       "Hermes-model caption; the handler closes over ctx.llm, so it is not a live or MCP call"),
    # --- godseye_state ---
    _t("godseye_live_get_current_view_state", "godseye_state", "get", "live", "later",
       "full view read; prefer the per-turn EOH map line to orient"),
    # --- godseye_admin ---
    _t("godseye_doctor", "godseye_admin", "doctor", "extra", "later", "read-only health check"),
    _t("godseye_rebuild_panel", "godseye_admin", "rebuild", "extra", "later", "rebuild dist/panel"),
    _t("godseye_live_reload_pane", "godseye_admin", "reload", "extra", "later", "reload the open pane"),
    _t("godseye_preview_swap", "godseye_admin", "preview", "extra", "later", "single desktop preview tab"),
)

BY_LEGACY: dict[str, Target] = {t.legacy: t for t in CATALOG}

_KEYS: dict[tuple[str, str], frozenset[str]] = {
    ("godseye_view", "fly"): frozenset({
        "locationId", "query", "latitude", "longitude", "viewMode", "rangeM", "waitForArrival", "referent",
        "framing", "fanout"}),
    ("godseye_view", "move"): frozenset({"motion", "direction", "speed", "mode", "fanout"}),
    ("godseye_view", "zoom"): frozenset({"direction", "amount", "fanout"}),
    ("godseye_view", "globe"): frozenset({"fanout"}),
    ("godseye_view", "home"): frozenset({"action", "lat", "lon", "altitude_m", "style", "layers"}),
    ("godseye_view", "share"): frozenset(),
    ("godseye_view", "panes"): frozenset({"prune", "label", "pane_id"}),
    ("godseye_query", "find_places"): frozenset({
        "query", "category", "cuisine", "near", "lat", "lon", "radiusM", "bbox", "limit", "pin", "intent",
        "context", "detail"}),
    ("godseye_query", "osm"): frozenset({"what", "areaId", "countOnly", "limit", "fanout", "detail"}),
    ("godseye_query", "context"): frozenset({"describe", "nearby", "category", "radius_m", "limit", "detail"}),
    ("godseye_query", "what_is_here"): frozenset({
        "lat", "lon", "radius_m", "category", "nearby", "limit", "detail"}),
    ("godseye_query", "brief"): frozenset({"area", "detail"}),
    ("godseye_query", "brief_and_show"): frozenset({
        "place", "question", "lat", "lon", "radius_km", "top_n", "military", "intent", "context", "pin_details",
        "detail"}),
    ("godseye_query", "analyst"): frozenset({
        "layers", "scope", "filters", "sortBy", "sortDir", "limit", "followUp", "fanout", "detail"}),
    ("godseye_query", "military"): frozenset({"location", "radius_km", "detail"}),
    ("godseye_query", "poi"): frozenset({"name", "city", "detail"}),
    ("godseye_query", "resolve_area"): frozenset({
        "query", "level", "within", "candidateId", "draw", "around", "radiusM", "flyTo", "fanout", "detail"}),
    ("godseye_query", "entity"): frozenset({"scope", "referent", "layerId", "limit", "fanout", "detail"}),
}

_REQUIRED = {
    ("godseye_view", "move"): ("motion",),
    ("godseye_view", "zoom"): ("direction", "amount"),
    ("godseye_query", "osm"): ("what",),
    ("godseye_query", "poi"): ("name",),
    ("godseye_query", "what_is_here"): ("lat", "lon"),
    ("godseye_query", "brief"): ("area",),
    ("godseye_query", "military"): ("location",),
}

_BLURB = {
    ("godseye_view", "fly"): "Fly to a place or coordinates. framing=street|rooftop|district. style (nvg or night vision = surveillance, flir = thermal) is applied after a successful fly.",
    ("godseye_view", "move"): "Nudge the camera: motion=orbit|pan|tilt|rotate|rotate_globe|stop.",
    ("godseye_view", "zoom"): "Relative zoom. direction=in|out and amount=little|medium|lot.",
    ("godseye_view", "globe"): "Whole Earth. Do not use zoom for this.",
    ("godseye_view", "home"): "Saved home view. action=go|set|get.",
    ("godseye_view", "share"): "Share link for the current view.",
    ("godseye_view", "panes"): "List panes, or set a label, or prune stale rows.",
    ("godseye_query", "find_places"): "POI search. here=true or a 'near me' phrase uses the pane camera (default 1500 m). pin=true drops pins.",
    ("godseye_query", "osm"): "In-pane OSM places of one kind (what=hospitals). Omit areaId to use the view.",
    ("godseye_query", "context"): "What the user is looking at: camera, focus, reverse geocode, nearby places. Use this for 'what am I looking at', this, and here.",
    ("godseye_query", "what_is_here"): "Reverse geocode a lat/lon. No pane required unless here=true.",
    ("godseye_query", "brief"): "Situation brief. Pass place, area, lat/lon, or here=true for the open view.",
    ("godseye_query", "brief_and_show"): "Brief a place and show it: fly, layers, top pins, caption.",
    ("godseye_query", "analyst"): "Counts and lists over enabled layers. Ships are ais-live-vessels; fires are local-firms.",
    ("godseye_query", "military"): "Military contacts around a place (aircraft, ships, installations).",
    ("godseye_query", "poi"): "Details for one venue: name, optional city. Prices are never invented.",
    ("godseye_query", "resolve_area"): "Named place to an areaId. draw=true outlines it. Pass candidateId when asked to choose.",
    ("godseye_query", "entity"): "Selected or in-view entity. scope=auto|selected|in_view|pointer.",
}


def phase_a_ops(composable: str) -> list[str]:
    return [t.op for t in CATALOG if t.composable == composable and t.phase == "A"]


def absorbed(legacy: str) -> bool:
    """True when this Hermes name is implemented as a godseye_view or godseye_query op.

    Those names are not registered. dispatch_legacy is the only entry that still accepts them.
    """
    target = BY_LEGACY.get(legacy)
    return target is not None and target.phase == "A"


def deprecation_note(legacy: str) -> str | None:
    """Internal mapping phrase. Not written into any schema the model sees."""
    target = BY_LEGACY.get(legacy)
    if target is None or target.phase != "A":
        return None
    return f"deprecated: use {target.composable} op={target.op}"


def alias_handler(legacy: str):
    """Retired-name entry. Forwards arguments unchanged to the op's handler."""
    spec = BY_LEGACY[legacy]

    def handler(args: dict, **kw) -> str:
        return invoke(spec, dict(args or {}), **kw)

    return handler


def dispatch_legacy(name: str, args: dict | None = None, **kw) -> str:
    """Run a retired Hermes tool name through the op that replaced it.

    Scenes, tours, /gev, the relay, and the pane use bare pane actions
    (fly_to_location, move_camera, get_current_view_state) and never see this.
    A saved call that still names godseye_live_fly_to_location or another
    absorbed tool comes here instead of the model catalog.
    """
    if not absorbed(name):
        return json.dumps({
            "ok": False, "error": "unknown_tool", "code": "unknown_tool",
            "message": "That name is not a registered tool.",
            "fix": "Use godseye_view or godseye_query.",
        })
    return alias_handler(name)(args or {}, **kw)


def composable_handler(composable: str):
    def handler(args: dict, **kw) -> str:
        args = dict(args or {})
        reason = refuse_args(args)
        if reason:
            return _fail(composable, {"ok": False, "error": reason})
        spec, prepared, extra, err = prepare(composable, args, kw.get("session_id"))
        if err:
            return _fail(composable, err)
        text = invoke(spec, prepared, **kw)
        style = (extra or {}).get("style")
        if style:
            text = _apply_style(text, style, **kw)
        return text

    return handler


def invoke(spec: Target, args: dict, **kw) -> str:
    """Call the handler the legacy tool already uses."""
    from . import hermes_tools as tools

    if spec.kind == "live":
        return tools.make_live_handler(spec.bare)(args, **kw)
    if spec.kind == "mcp":
        return tools.make_handler(spec.bare)(args, **kw)
    if spec.kind == "extra":
        return tools.extra_handler(spec.legacy)(args, **kw)
    raise RuntimeError(f"{spec.legacy} ({spec.kind}) is not dispatched in phase A")


def prepare(composable: str, args: dict, session_id=None):
    """Return ``(target, forwarded_args, extra, error)``.

    ``extra`` may hold ``style`` for ``op=fly``. ``error`` is a dict when the
    op or the arguments cannot be forwarded. The alias path does not call this.
    """
    args = dict(args or {})
    op = args.pop("op", None)
    ops = phase_a_ops(composable)
    if not isinstance(op, str) or not op or op not in ops:
        if not isinstance(op, str) or not op:
            message = f"{composable} needs an op."
        else:
            message = f"{composable} has no op {op!r}."
        return None, None, None, {
            "ok": False,
            "error": "unknown_op",
            "message": message,
            "fix": "Pass op as one of: " + ", ".join(ops) + ".",
        }
    spec = next(t for t in CATALOG if t.composable == composable and t.op == op and t.phase == "A")
    extra: dict = {}
    if op == "fly" and composable == "godseye_view":
        raw_style = args.pop("style", None)
        if raw_style is not None:
            mapped = canonical_style(raw_style)
            if not mapped:
                return None, None, None, _bad_style(raw_style)
            extra["style"] = mapped
        if args.get("latitude") is None and args.get("lat") is not None:
            args["latitude"] = args.pop("lat")
            if args.get("longitude") is None and args.get("lon") is not None:
                args["longitude"] = args.pop("lon")
        args.pop("lat", None)
        args.pop("lon", None)
    normalizer = _NORMALIZERS.get((composable, op), _drop_here)
    args, err = normalizer(args, session_id)
    if err:
        return None, None, None, err
    allowed = _KEYS[(composable, op)]
    forwarded = {k: v for k, v in args.items() if k in allowed}
    missing = [key for key in _REQUIRED.get((composable, op), ()) if forwarded.get(key) in (None, "")]
    if missing:
        return None, None, None, {
            "ok": False,
            "error": "bad_args",
            "message": f"{composable} op={op} needs {', '.join(missing)}.",
            "fix": f"Retry {composable} op={op} with {', '.join(missing)} set.",
        }
    for key, choices in _VALUE_ENUMS.get((composable, op), {}).items():
        if key in forwarded and forwarded[key] not in choices:
            return None, None, None, {
                "ok": False,
                "error": "bad_args",
                "message": f"{composable} op={op} {key}={forwarded[key]!r} is not one of {', '.join(choices)}.",
                "fix": f"Retry {composable} op={op} with {key} set to one of: {', '.join(choices)}.",
            }
    return spec, forwarded, extra, None


_VALUE_ENUMS = {
    ("godseye_view", "fly"): {
        "framing": ("street", "rooftop", "district"),
        "viewMode": ("close", "overview"),
        "locationId": ("austin", "sf", "nyc", "tokyo", "london", "paris", "dubai", "dc"),
    },
    ("godseye_view", "move"): {
        "motion": ("orbit", "pan", "tilt", "rotate", "rotate_globe", "stop"),
        "direction": ("left", "right", "up", "down"),
        "speed": ("slow", "normal", "fast"),
        "mode": ("once", "continuous"),
    },
    ("godseye_view", "zoom"): {"direction": ("in", "out"), "amount": ("little", "medium", "lot")},
    ("godseye_view", "home"): {"action": ("go", "set", "get")},
}


def canonical_style(value) -> str | None:
    key = str(value).strip().lower()
    mapped = _STYLE_ALIASES.get(key, key)
    return mapped if mapped in _STYLES else None


def refuse_args(args: dict) -> str | None:
    """Same person-tracking rule as the legacy tools, applied to nested strings too."""
    found: list[str] = []
    _collect_strings(args, found)
    if not found:
        return None
    return policy.refuse_reason({"q": " ".join(found)})


def camera(session_id):
    """Current pane camera via the same ``get_current_view_state`` call other tools use.

    Returns ``(point, error)``. ``point`` has lat, lon, and rangeM when known.
    """
    from . import composer

    st = composer.live_call("get_current_view_state", {}, session_id)
    if not isinstance(st, dict) or not st.get("ok"):
        if isinstance(st, dict):
            return None, st
        return None, {"ok": False, "error": "no_pane", "message": "No Eye Of Hermes pane answered."}
    result = st.get("result") if isinstance(st.get("result"), dict) else {}
    cam = result.get("camera") if isinstance(result.get("camera"), dict) else {}
    lat = cam.get("targetLat", cam.get("latitude", cam.get("lat")))
    lon = cam.get("targetLon", cam.get("longitude", cam.get("lon")))
    lat_ok = isinstance(lat, (int, float)) and not isinstance(lat, bool)
    lon_ok = isinstance(lon, (int, float)) and not isinstance(lon, bool)
    if not lat_ok or not lon_ok:
        return None, {
            "ok": False,
            "error": "no_focus",
            "message": "The open pane has no camera position yet.",
            "fix": "Fly somewhere or click the globe, then retry with here=true.",
        }
    rng = cam.get("rangeM")
    if not isinstance(rng, (int, float)) or isinstance(rng, bool):
        rng = cam.get("heightM")
    point = {"lat": float(lat), "lon": float(lon), "rangeM": None}
    if isinstance(rng, (int, float)) and not isinstance(rng, bool):
        point["rangeM"] = float(rng)
    return point, None


def _radius_km(point: dict) -> float:
    rng = point.get("rangeM")
    if not isinstance(rng, (int, float)) or rng <= 0:
        return 25.0
    return max(1.0, min(100.0, float(rng) / 1000.0))


def _is_here(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in _HERE


def _drop_here(args: dict, _session_id):
    args.pop("here", None)
    return args, None


def _norm_home(args: dict, _session_id):
    args.pop("here", None)
    if args.get("style") is not None:
        mapped = canonical_style(args["style"])
        if not mapped:
            return None, _bad_style(args["style"])
        args["style"] = mapped
    return args, None


def _norm_find_places(args: dict, session_id):
    here = bool(args.pop("here", False))
    near = args.get("near")
    if isinstance(near, str) and _is_here(near):
        here = True
        args.pop("near", None)
    query = args.get("query")
    if isinstance(query, str) and query.strip():
        parts = _SPLIT.split(query.strip(), maxsplit=1)
        if len(parts) == 2 and _is_here(parts[1]):
            here = True
            args["query"] = parts[0].strip()
    if here and args.get("lat") is None and not args.get("bbox") and not isinstance(args.get("near"), dict):
        point, err = camera(session_id)
        if err:
            return None, err
        args["lat"] = point["lat"]
        args["lon"] = point["lon"]
        args.setdefault("radiusM", 1500)
    return args, None


def _norm_brief(args: dict, session_id):
    here = bool(args.pop("here", False))
    place = args.pop("place", None)
    lat, lon = args.pop("lat", None), args.pop("lon", None)
    radius = args.pop("radius_km", None)
    args.pop("question", None)
    if args.get("area"):
        return args, None
    if _is_here(place):
        here = True
        place = None
    if place:
        args["area"] = {"place": place}
        return args, None
    if lat is not None and lon is not None:
        area = {"lat": lat, "lon": lon}
        if radius is not None:
            area["radius_km"] = radius
        args["area"] = area
        return args, None
    if here:
        point, err = camera(session_id)
        if err:
            return None, err
        args["area"] = {"lat": point["lat"], "lon": point["lon"], "radius_km": radius if radius is not None else _radius_km(point)}
        return args, None
    return None, {
        "ok": False,
        "error": "bad_args",
        "message": "brief needs a place, lat and lon, area, or here=true.",
        "fix": "Retry godseye_query op=brief with place, area, lat/lon, or here=true for the open pane.",
    }


def _norm_military(args: dict, session_id):
    here = bool(args.pop("here", False))
    place = args.pop("place", None)
    lat, lon = args.pop("lat", None), args.pop("lon", None)
    if args.get("location"):
        return args, None
    if _is_here(place):
        here = True
        place = None
    if place:
        args["location"] = {"place": place}
        return args, None
    if lat is not None and lon is not None:
        args["location"] = {"lat": lat, "lon": lon}
        return args, None
    if here:
        point, err = camera(session_id)
        if err:
            return None, err
        args["location"] = {"lat": point["lat"], "lon": point["lon"]}
        return args, None
    return None, {
        "ok": False,
        "error": "bad_args",
        "message": "military needs a place, lat and lon, location, or here=true.",
        "fix": "Retry godseye_query op=military with location, place, lat/lon, or here=true.",
    }


def _norm_show(args: dict, session_id):
    """brief_and_show and what_is_here: fill lat/lon from the camera when asked."""
    here = bool(args.pop("here", False))
    if _is_here(args.get("place")):
        here = True
        args.pop("place", None)
    if here and args.get("lat") is None and not args.get("place"):
        point, err = camera(session_id)
        if err:
            return None, err
        args["lat"] = point["lat"]
        args["lon"] = point["lon"]
        args.setdefault("radius_km", _radius_km(point))
    return args, None


def _norm_what(args: dict, session_id):
    here = bool(args.pop("here", False))
    if here and args.get("lat") is None:
        point, err = camera(session_id)
        if err:
            return None, err
        args["lat"] = point["lat"]
        args["lon"] = point["lon"]
    return args, None


_NORMALIZERS = {
    ("godseye_view", "home"): _norm_home,
    ("godseye_query", "find_places"): _norm_find_places,
    ("godseye_query", "brief"): _norm_brief,
    ("godseye_query", "military"): _norm_military,
    ("godseye_query", "brief_and_show"): _norm_show,
    ("godseye_query", "what_is_here"): _norm_what,
}


def _bad_style(value) -> dict:
    return {
        "ok": False,
        "error": "bad_args",
        "message": f"Unknown style {value!r}.",
        "fix": "Use normal, retro, surveillance (NVG or night vision), thermal (FLIR), anime, or noir.",
    }


def _collect_strings(value, found: list[str]) -> None:
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_strings(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_strings(item, found)


def _fail(tool: str, payload: dict) -> str:
    from . import errors

    return json.dumps(errors.annotate(payload, tool=tool), ensure_ascii=False)


def _apply_style(fly_text: str, style: str, **kw) -> str:
    try:
        body = json.loads(fly_text)
    except (TypeError, ValueError):
        return fly_text
    if not isinstance(body, dict) or body.get("ok") is False or body.get("error"):
        return fly_text
    styled = invoke(BY_LEGACY["godseye_live_set_visual_style"], {"style": style}, **kw)
    try:
        body["style"] = json.loads(styled)
    except (TypeError, ValueError):
        body["style"] = {"ok": False, "error": str(styled)[:300]}
    return json.dumps(body, ensure_ascii=False)


def _prop(type_: str, description: str, **extra) -> dict:
    out = {"type": type_, "description": description}
    out.update(extra)
    return out


def _schema(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["op"],
            "properties": properties,
        },
    }


def _tool_description(composable: str, lead: str) -> str:
    lines = [lead, ""]
    for op in phase_a_ops(composable):
        lines.append(f"op={op}: {_BLURB[(composable, op)]}")
    lines.append("")
    lines.append(policy.POLICY)
    return "\n".join(lines)


def view_schema() -> dict:
    ops = phase_a_ops("godseye_view")
    props = {
        "op": _prop("string", "Which camera or pane action to run.", enum=ops),
        "query": _prop("string", "fly: place name, for example Tokyo or Strait of Hormuz."),
        "locationId": _prop("string", "fly: preset city id when it matches.", enum=[
            "austin", "sf", "nyc", "tokyo", "london", "paris", "dubai", "dc"]),
        "latitude": _prop("number", "fly: WGS84 latitude. lat is accepted and copied here.", minimum=-90, maximum=90),
        "longitude": _prop("number", "fly: WGS84 longitude. lon is accepted and copied here.", minimum=-180, maximum=180),
        "lat": _prop("number", "fly alias of latitude. home: latitude when action=set.", minimum=-90, maximum=90),
        "lon": _prop("number", "fly alias of longitude. home: longitude when action=set.", minimum=-180, maximum=180),
        "framing": _prop("string", "fly: street (150 m, pitch -15), rooftop (450 m, -30), or district (2500 m, -45).",
                         enum=["street", "rooftop", "district"]),
        "rangeM": _prop("number", "fly: camera range in meters. Overrides framing. Minimum 100.", minimum=100, maximum=20000000),
        "viewMode": _prop("string", "fly: close or overview. Usually omit; the globe infers it.", enum=["close", "overview"]),
        "waitForArrival": _prop("boolean", "fly: wait until the camera arrives before returning."),
        "referent": _prop("integer", "fly: numbered item from the last result list."),
        "style": _prop("string", "fly: set after a successful fly. home: saved style when action=set. "
                       "nvg, night, night vision, and night-vision mean surveillance. flir, ir, and infrared mean thermal.",
                       enum=["normal", "retro", "surveillance", "thermal", "anime", "noir",
                             "nvg", "night", "night vision", "night-vision", "nightvision", "flir", "ir", "infrared"]),
        "motion": _prop("string", "move: orbit, pan, tilt, rotate, rotate_globe, or stop.",
                        enum=["orbit", "pan", "tilt", "rotate", "rotate_globe", "stop"]),
        "direction": _prop("string", "zoom: in or out. move: left, right, up, or down (orbit defaults to right).",
                           enum=["in", "out", "left", "right", "up", "down"]),
        "speed": _prop("string", "move: slow, normal, or fast.", enum=["slow", "normal", "fast"]),
        "mode": _prop("string", "move: once (a nudge) or continuous (until stop).", enum=["once", "continuous"]),
        "amount": _prop("string", "zoom: little, medium, or lot.", enum=["little", "medium", "lot"]),
        "action": _prop("string", "home: go (default), set, or get.", enum=["go", "set", "get"]),
        "altitude_m": _prop("number", "home action=set: camera height in meters.", minimum=100, maximum=40000000),
        "layers": _prop("array", "home action=set: layer ids to save.", items={"type": "string"}),
        "prune": _prop("boolean", "panes: drop stale closed pane records now."),
        "label": _prop("string", "panes: label for this chat's pane."),
        "pane_id": _prop("string", "panes: pane to label. Default is this session's pane."),
        "fanout": _prop("boolean", "fly, move, zoom, globe: send to every open pane. Default false."),
    }
    return _schema("godseye_view", _tool_description(
        "godseye_view",
        "Move the Eye Of Hermes (EOH) camera, home view, share link, or pane list. One op per call."), props)


def query_schema() -> dict:
    from . import detail

    ops = phase_a_ops("godseye_query")
    scope = {
        "description": "analyst: object with kind view|region|radius|anywhere|pointer|area|drawn|annotation. "
                       "entity: string auto|selected|in_view|pointer.",
        "anyOf": [
            {"type": "string", "enum": ["auto", "selected", "in_view", "pointer"]},
            {"type": "object", "additionalProperties": False, "properties": {
                "kind": {"type": "string", "enum": [
                    "view", "region", "radius", "anywhere", "pointer", "area", "drawn", "annotation"]},
                "name": {"type": "string"},
                "areaId": {"type": "string"},
                "id": {"type": "string"},
                "km": {"type": "number"},
                "center": {"type": "object", "properties": {
                    "lat": {"type": "number"}, "lon": {"type": "number"}}, "required": ["lat", "lon"]},
            }},
        ],
    }
    props = {
        "op": _prop("string", "Which lookup to run.", enum=ops),
        "here": _prop("boolean", "Use the open pane's camera. find_places searches around it (default 1500 m). "
                     "brief, military, brief_and_show, and what_is_here fill lat/lon. "
                     "A query or near value of me/here/this area does the same."),
        "detail": dict(detail.DETAIL_PROP),
        "fanout": _prop("boolean", "osm, analyst, resolve_area, entity: send to every open pane. Default false."),
        "query": _prop("string", "find_places: 'ramen in Montreal' or 'ramen' with here=true. resolve_area: the place to outline."),
        "category": _prop("string", "find_places category, or a nearby filter for context / what_is_here (restaurant, cafe, hotel)."),
        "cuisine": _prop("string", "find_places cuisine, for example ramen."),
        "near": {"description": "find_places: place name, {lat, lon}, or me/here.", "anyOf": [
            {"type": "string"},
            {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}, "name": {"type": "string"}},
             "required": ["lat", "lon"]}]},
        "lat": _prop("number", "Latitude. brief and military accept this instead of a nested area or location.", minimum=-90, maximum=90),
        "lon": _prop("number", "Longitude.", minimum=-180, maximum=180),
        "radiusM": _prop("number", "find_places or resolve_area radius in meters.", minimum=50, maximum=20000),
        "bbox": _prop("array", "find_places box [south, west, north, east].", items={"type": "number"}, minItems=4, maxItems=4),
        "limit": _prop("number", "Maximum rows (find_places, osm, context, what_is_here, analyst, entity)."),
        "pin": _prop("boolean", "find_places: pin the results on the open pane."),
        "intent": _prop("string", "find_places and brief_and_show: the user's request, used to fill pin drawers."),
        "context": _prop("string", "Alias of intent on find_places and brief_and_show. This is not op=context."),
        "what": _prop("string", "osm: the kind of place, for example hospitals or cafes."),
        "areaId": _prop("string", "osm: area from resolve_area. Omit to use the current view."),
        "countOnly": _prop("boolean", "osm: return a count only."),
        "describe": _prop("boolean", "context: reverse geocode the focus. Default true."),
        "nearby": _prop("boolean", "context and what_is_here: include nearby named places. Default true."),
        "radius_m": _prop("number", "context and what_is_here: nearby radius in meters.", minimum=30, maximum=2000),
        "place": _prop("string", "brief and brief_and_show: place name. 'here' or 'this area' uses the pane camera."),
        "question": _prop("string", "brief_and_show: the user's question. Military wording selects military_awareness."),
        "radius_km": _prop("number", "brief, brief_and_show, or military radius in kilometers."),
        "top_n": _prop("integer", "brief_and_show: how many hits to pin.", minimum=0, maximum=24),
        "military": _prop("boolean", "brief_and_show: force military_awareness (true) or situation_brief (false)."),
        "pin_details": _prop("array", "brief_and_show: optional drawer content per hit, in hit order.",
                            items={"type": "object"}),
        "area": {"type": "object", "description": "brief: {place} or {lat, lon, radius_km} or {bbox}.",
                 "properties": {
                     "place": {"type": "string"},
                     "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                     "lat": {"type": "number"}, "lon": {"type": "number"}, "radius_km": {"type": "number"}}},
        "layers": _prop("array", "analyst: layer ids. flights, military, ais-live-vessels, local-firms, earthquakes, satellites.",
                        items={"type": "string"}),
        "scope": scope,
        "filters": _prop("array", "analyst: ANDed {field, op, value}. op is gt, gte, lt, lte, eq, neq, or contains.",
                         items={"type": "object", "properties": {
                             "field": {"type": "string"},
                             "op": {"type": "string", "enum": ["gt", "gte", "lt", "lte", "eq", "neq", "contains"]},
                             "value": {}}, "required": ["field", "op", "value"]}),
        "sortBy": _prop("string", "analyst: field name, or distance."),
        "sortDir": _prop("string", "analyst: asc or desc.", enum=["asc", "desc"]),
        "followUp": _prop("boolean", "analyst: re-filter the previous answer."),
        "location": {"type": "object", "description": "military: {place} or {lat, lon}.",
                     "properties": {"place": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}}},
        "name": _prop("string", "poi: venue name."),
        "city": _prop("string", "poi: city, when the name is ambiguous."),
        "level": _prop("string", "resolve_area: country, admin1, admin2, city, district, natural, or site.",
                       enum=["country", "admin1", "admin2", "city", "district", "natural", "site"]),
        "within": _prop("string", "resolve_area: country or state the user added."),
        "candidateId": _prop("string", "resolve_area: chosen candidate when the first call asked which place."),
        "draw": _prop("boolean", "resolve_area: outline the area on the pane."),
        "around": _prop("boolean", "resolve_area: buffer around a point place."),
        "flyTo": _prop("boolean", "resolve_area: also fly to the area."),
        "referent": _prop("integer", "entity: numbered item from the last result list."),
        "layerId": _prop("string", "entity: optional layer filter."),
    }
    return _schema("godseye_query", _tool_description(
        "godseye_query",
        "Look up places, the open view, and situation briefs. One op per call. "
        "Public aircraft, vessels, places, and hazards only. Do not search for a person."), props)


def register_composable(ctx) -> None:
    specs = (
        ("godseye_view", view_schema(), "Camera, home, share, and panes", "🌐"),
        ("godseye_query", query_schema(), "Places, context, and briefs", "🔎"),
    )
    for name, schema, blurb, emoji in specs:
        ctx.register_tool(
            name=name, toolset="godseye", schema=schema,
            handler=composable_handler(name), description=blurb, emoji=emoji)
