# Composable tools

The model catalog is eleven tools. Each takes a required `op` and calls the same handler the old name used (`make_live_handler`, `make_handler`, `extra_handler`, or the HUD closure in `gev/hermes_tools.py`). Every legacy name in the table is absorbed: it is not registered and it is not listed in `plugin.yaml` `provides_tools`.

`router.dispatch_legacy(name, args)` is the internal map. It accepts a retired Hermes name and runs that op's handler with the original arguments. Saved scenes, tours, `/gev`, the relay, and the pane scripts do not use Hermes tool names. They keep the bare pane actions (`fly_to_location`, `move_camera`, `get_current_view_state`, `hermes_context`).

`godseye_state` is not a tool. The full camera read is `godseye_view` `op=state`. The per-turn EOH map line is the orientation path. `op=state` with `detail=full` is the raw object when that line is missing.

`godseye_investigation_export` is `godseye_scene` `op=export` (GeoJSON, KML, and the optional pack). It is not a separate registered tool.

`godseye_nav` `op=timeline` is the Wayback and event-history slider. The sub-action is an argument named `timeline` (`set`, `play`, `step`, `stop`, `state`, `list_dates`). `op=compare` stays the imagery swipe. `op=street_walk` walks a saved route in the Street Level viewer. Its sub-action is an argument named `walk` (`start`, `pause`, `resume`, `step`, `stop`, `state`). `op=street_level` opens one panorama. Its sub-action is an argument named `street` (`at`, `next`, `prev`, `route`).

Plugin id stays `godseye`. Tool names keep the `godseye_` prefix.

## What is registered

| Tool | Ops |
|---|---|
| `godseye_view` | `fly`, `move`, `zoom`, `globe`, `home`, `share`, `panes`, `show`, `state` |
| `godseye_query` | `find_places`, `osm`, `context`, `what_is_here`, `brief`, `brief_and_show`, `analyst`, `military`, `poi`, `resolve_area`, `entity`, `terrain`, `features` |
| `godseye_layers` | `visibility`, `menu`, `panel`, `context_mode`, `style`, `sonar`, `detection`, `stack`, `post` |
| `godseye_feeds` | `earthquakes`, `fires`, `launches`, `aircraft_in_area`, `find_aircraft`, `aircraft_track`, `aircraft_info`, `vessels_in_area`, `find_vessel`, `vessel_track`, `next_pass`, `overhead`, `find_cctv`, `cctv_snapshot`, `alpr`, `traffic`, `weather`, `imagery`, `cables`, `infrastructure`, `flood`, `cyclones`, `fire_perimeters`, `military_sites`, `imagery_live`, `iss_pass`, `satellite_pass`, `cctv_control`, `radio` |
| `godseye_annotate` | `annotate`, `clear`, `draw`, `list`, `delete`, `update`, `filter`, `drawer`, `research` |
| `godseye_nav` | `measure`, `isochrone`, `fly_route`, `street_walk`, `street_level`, `itinerary`, `compare`, `timeline` |
| `godseye_track` | `track`, `stop`, `select_nearest`, `frame`, `cockpit` |
| `godseye_scene` | `save`, `list`, `load`, `delete`, `report`, `export`, `restore`, `tour`, `tour_status`, `tour_stop`, `playback` |
| `godseye_watch` | `create`, `list`, `pause`, `delete` |
| `godseye_capture` | `hud`, `summary`, `pane` |
| `godseye_admin` | `doctor`, `batch`, `keys`, `rebuild`, `reload`, `preview`, `status`, `app_start`, `app_stop`, `app_restart` |

`op=fly` accepts `style`. `nvg`, `night`, `night vision`, and `night-vision` mean `surveillance`. `flir`, `ir`, and `infrared` mean `thermal`. After a successful fly the router calls the existing `set_visual_style` handler. A failed fly does not change the style. `godseye_layers` `op=style` is the same handler. The same aliases are accepted there.

`here=true` on `find_places`, `brief`, `brief_and_show`, `military`, and `what_is_here` reads the open pane with `get_current_view_state` and then calls the original handler. A query or `near` value of `me`, `here`, or `this area` does the same. `find_places` then searches within 1500 m unless `radiusM` is set. `brief` uses the camera range (clamped to 1–100 km, or 25 km when the range is unknown) as `radius_km`.

The composable entry applies the same person-tracking refusal as the rest of the plugin, including strings nested inside `area` or `location`. It refuses before any pane or network call. Feed ops keep that rule. CCTV and ALPR stay mapped public cameras: the feeds description says they are not a way to identify a person, and the handlers still run `policy.refuse_reason`.

`godseye_nav` `op=measure` takes the old inner `op` (`distance`, `area`, or `route`) as an argument named `measure`, then puts it back to `op` before the handler. `dispatch_legacy` for the retired measure name still forwards the old `op` unchanged. `op=timeline` does the same with an argument named `timeline` (`set`, `play`, `step`, `stop`, `state`, `list_dates`). `op=street_walk` does the same with an argument named `walk` (`start`, `pause`, `resume`, `step`, `stop`, `state`). `op=street_level` does the same with an argument named `street` (`at`, `next`, `prev`, `route`). `dispatch_legacy` for those absorbed names still forwards `op`.

`godseye_capture` `op=summary` closes over `ctx.llm`. `register_tools` binds that closure. It is not a live or MCP dispatch.

## Adjustments from the research table

The starting table is in the 2026-11-05 research note, section 1. These rows differ because of the handlers in this tree:

- **Framing** is the `framing` argument on `fly` (`street`, `rooftop`, `district`), not an op. `godseye_live_frame_overhead` stays on `godseye_track` (`op=frame`): it frames nearby aircraft, ships, or satellites.
- **`zoom_to_globe` is `op=globe`**, not `zoom`. The live tool's own description says a relative zoom cannot reach the whole Earth.
- **`fly_route` stays on `godseye_nav`.** It dollies along a route that was already drawn. It is not a camera fly. A dense line is thinned and sent in chunks of about 300 points. `lighten` defaults on: Street Level is turned off and the globe is coarsened until the dolly ends.
- **`street_walk` is `godseye_nav`.** The sub-action is the argument `walk`, so it does not collide with `op`. It samples a saved route and opens the nearest Street View panorama at each point.
- **`street_level` is `godseye_nav`.** The sub-action is the argument `street` (`at`, `next`, `prev`, `route`). One hop per call. `itinerary` builds a drive or walk through pins and can hand the line to the chunked dolly.
- **`annotate` reads a dataset.** `source` is a `.jsonl`, `.csv`, or `.json` path. `records` is a list of `{lat, lon, label, description_md, images, links, tags, category, id}`. `replace: true` matches a stable `id` or a pin within 25 m. Exact duplicates are skipped. `op=update` patches one pin without a redraw. `op=delete` accepts `ids`.
- **Boxes take named keys.** `{south, west, north, east}` is accepted everywhere a bbox is taken. A four-number list is checked and rejected when neither order is a real box. `area: {place, radius_km}` is geocoded. `op=doctor` with `probe: true` calls each keyed provider once, including TomTom. `op=keys` reads a path and never a secret value. `op=batch` runs up to 16 composable calls. `op=pane` returns a PNG of the pane.
- **`resolve_area`, `poi_details`, and `get_entity_context` are query ops.** `resolve_area` is how `analyst` and `osm` get an `areaId`. `poi_details` is the one-venue step after `find_places`. Entity context is the "entity" role in the research note.
- **`get_terrain_height` and `get_map_features` are query ops** (`terrain`, `features`). They are point lookups, not feeds.
- **`show_in_gods_eye_view` is `godseye_view` `op=show`.** The registered schema keeps the argument names and drops the nested MCP descriptions, so the view tool stays small.
- **The camera read is `godseye_view` `op=state`.** There is no `godseye_state` tool. The per-turn map line covers orientation.
- **Panels, Contacts mode, sonar, and detection sit with layers.** They change pane chrome or a visual overlay. Cockpit sits with track. Cinematic `control_scene` sits with scene (`op=playback`), separate from investigation save/load. CCTV control and radio sit with feeds.
- **Two satellite-pass tools.** `next_satellite_pass` is the MCP call (`op=next_pass`, no pane). The live catalog pass is `op=satellite_pass`. The ISS pass is `op=iss_pass`.
- **Watch check is not a tool.** `godseye_watch` `op=list` takes `check=<id>`.
- **`op=timeline` is `godseye_nav`.** The sub-action is the argument `timeline`, so it does not collide with `op`. `op=compare` is unchanged.

## Selection eval

`evals/selection_golden.json` has one task per op. `tests/test_composable.py` checks schemas, router dispatch, alias parity, and that golden file offline. `evals/run_selection.py` sends the same set to a live model when `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set. It exits 0 without calling a model when no key is set, and whenever `CI` or `GITHUB_ACTIONS` is set. `evals/measure_schemas.py` prints schema bytes and token counts (tiktoken `o200k_base` and `cl100k_base` when installed).

## Full map

Phase `A` was the first cut (`godseye_view` and `godseye_query`, without `show`, `state`, `terrain`, or `features`). Phase `B` is the rest, including `timeline`. Both are registered.

| Legacy tool | Composable | op | Phase | Notes |
|---|---|---|---|---|
| `godseye_live_fly_to_location` | `godseye_view` | `fly` | A | framing is an argument on fly (street, rooftop, district), not its own op |
| `godseye_live_move_camera` | `godseye_view` | `move` | A | orbit, pan, tilt, rotate, rotate_globe, or stop |
| `godseye_live_adjust_camera_zoom` | `godseye_view` | `zoom` | A | relative step only; whole-Earth is op=globe |
| `godseye_live_zoom_to_globe` | `godseye_view` | `globe` | A | separate op because a relative zoom cannot reach the globe |
| `godseye_home` | `godseye_view` | `home` | A | action stays go, set, or get |
| `godseye_share_link` | `godseye_view` | `share` | A | share link for the open view |
| `godseye_live_panes` | `godseye_view` | `panes` | A | list, label, or prune pane records |
| `godseye_show_in_gods_eye_view` | `godseye_view` | `show` | B | tight arguments: view, area, camera, layers, style, map, annotations, follow |
| `godseye_live_set_layer_visibility` | `godseye_layers` | `visibility` | B | one layer on or off |
| `godseye_live_show_data_layers_menu` | `godseye_layers` | `menu` | B | open the layers menu |
| `godseye_live_set_panel_open` | `godseye_layers` | `panel` | B | side panels are chrome, grouped with the menus |
| `godseye_live_set_context_mode` | `godseye_layers` | `context_mode` | B | Contacts or Space Missions mode, not a data query |
| `godseye_live_set_visual_style` | `godseye_layers` | `style` | B | home of style; fly also calls this handler after a successful fly |
| `godseye_live_set_cyber_sonar` | `godseye_layers` | `sonar` | B | HUD decoration, not a feed |
| `godseye_live_set_detection` | `godseye_layers` | `detection` | B | detection overlay density |
| `godseye_live_set_map_stack` | `godseye_layers` | `stack` | B | basemap stack |
| `godseye_live_set_post_processing` | `godseye_layers` | `post` | B | post-processing toggles |
| `godseye_find_places` | `godseye_query` | `find_places` | A | keyless POI search; here=true or 'near me' uses the pane camera |
| `godseye_live_osm_query` | `godseye_query` | `osm` | A | in-pane OSM places; defaults to the current view |
| `godseye_live_context` | `godseye_query` | `context` | A | what the pane is looking at, including reverse geocode |
| `godseye_what_is_here` | `godseye_query` | `what_is_here` | A | reverse geocode a coordinate; no pane unless here=true |
| `godseye_situation_brief` | `godseye_query` | `brief` | A | situation_brief; place, area, or here=true |
| `godseye_brief_and_show` | `godseye_query` | `brief_and_show` | A | brief plus fly, layers, pins, and caption |
| `godseye_live_analyst_query` | `godseye_query` | `analyst` | A | counts and lists over enabled layers |
| `godseye_military_awareness` | `godseye_query` | `military` | A | military contacts around a place |
| `godseye_poi_details` | `godseye_query` | `poi` | A | one venue after find_places; not a feed |
| `godseye_live_resolve_area` | `godseye_query` | `resolve_area` | A | areaId for analyst and osm; omitted from the research table |
| `godseye_live_get_entity_context` | `godseye_query` | `entity` | A | selected or in-view records; the research query role named entity |
| `godseye_get_terrain_height` | `godseye_query` | `terrain` | B | point elevation, not a streaming feed |
| `godseye_get_map_features` | `godseye_query` | `features` | B | named OSM features at a point, sibling of what_is_here |
| `godseye_get_earthquakes` | `godseye_feeds` | `earthquakes` | B | USGS events |
| `godseye_get_active_fires` | `godseye_feeds` | `fires` | B | NASA FIRMS hotspots |
| `godseye_get_recent_launches` | `godseye_feeds` | `launches` | B | recent rocket launches |
| `godseye_aircraft_in_area` | `godseye_feeds` | `aircraft_in_area` | B | aircraft in an area |
| `godseye_find_aircraft` | `godseye_feeds` | `find_aircraft` | B | aircraft lookup |
| `godseye_get_aircraft_track` | `godseye_feeds` | `aircraft_track` | B | one aircraft track |
| `godseye_get_aircraft_info` | `godseye_feeds` | `aircraft_info` | B | one aircraft record |
| `godseye_vessels_in_area` | `godseye_feeds` | `vessels_in_area` | B | ships in an area |
| `godseye_find_vessel` | `godseye_feeds` | `find_vessel` | B | ship lookup |
| `godseye_get_vessel_track` | `godseye_feeds` | `vessel_track` | B | one ship track |
| `godseye_next_satellite_pass` | `godseye_feeds` | `next_pass` | B | MCP pass prediction; no pane. The live tool is op=satellite_pass |
| `godseye_satellites_overhead` | `godseye_feeds` | `overhead` | B | satellites above a point |
| `godseye_find_cctv_cameras` | `godseye_feeds` | `find_cctv` | B | public CCTV camera search |
| `godseye_get_cctv_snapshot` | `godseye_feeds` | `cctv_snapshot` | B | one camera snapshot |
| `godseye_find_alpr_cameras` | `godseye_feeds` | `alpr` | B | ALPR camera search |
| `godseye_get_traffic_flow` | `godseye_feeds` | `traffic` | B | traffic flow |
| `godseye_get_weather_map` | `godseye_feeds` | `weather` | B | weather raster |
| `godseye_get_recent_imagery` | `godseye_feeds` | `imagery` | B | MCP recent imagery. The pane tool is op=imagery_live |
| `godseye_find_submarine_cables` | `godseye_feeds` | `cables` | B | submarine cables |
| `godseye_find_infrastructure` | `godseye_feeds` | `infrastructure` | B | mapped infrastructure |
| `godseye_get_bhote_koshi_flood` | `godseye_feeds` | `flood` | B | single flood event, not a general query |
| `godseye_get_cyclones` | `godseye_feeds` | `cyclones` | B | tropical cyclones |
| `godseye_get_fire_perimeters` | `godseye_feeds` | `fire_perimeters` | B | wildfire perimeters |
| `godseye_find_military_installations` | `godseye_feeds` | `military_sites` | B | OSM installation catalog; the brief is godseye_query op=military |
| `godseye_live_find_imagery` | `godseye_feeds` | `imagery_live` | B | pane recent-imagery search |
| `godseye_live_next_iss_pass` | `godseye_feeds` | `iss_pass` | B | next ISS pass from the pane |
| `godseye_live_next_satellite_pass` | `godseye_feeds` | `satellite_pass` | B | next pass from the loaded pane catalog |
| `godseye_live_control_cctv` | `godseye_feeds` | `cctv_control` | B | pane CCTV controls; snapshots stay op=cctv_snapshot |
| `godseye_live_control_radio` | `godseye_feeds` | `radio` | B | internet radio playback |
| `godseye_live_annotate_map` | `godseye_annotate` | `annotate` | B | pins, routes, cards |
| `godseye_live_clear_annotations` | `godseye_annotate` | `clear` | B | clear marks on the pane |
| `godseye_live_draw` | `godseye_annotate` | `draw` | B | user draw mode or Hermes shapes |
| `godseye_live_list_annotations` | `godseye_annotate` | `list` | B | stored marks for this chat |
| `godseye_live_delete_annotation` | `godseye_annotate` | `delete` | B | delete one stored mark, or ids for several |
| `godseye_live_update_annotation` | `godseye_annotate` | `update` | B | patch one pin without a redraw |
| `godseye_live_filter_pins` | `godseye_annotate` | `filter` | B | category, rating, open-now chips |
| `godseye_live_pin_drawer` | `godseye_annotate` | `drawer` | B | open or close one pin drawer |
| `godseye_research_pins` | `godseye_annotate` | `research` | B | fill drawers from free sources |
| `godseye_measure` | `godseye_nav` | `measure` | B | argument measure is distance, area, or route so it does not collide with op |
| `godseye_isochrone` | `godseye_nav` | `isochrone` | B | minutes of reach |
| `godseye_live_fly_route` | `godseye_nav` | `fly_route` | B | dolly along an existing route; dense lines are thinned and chunked; not a camera fly |
| `godseye_street_walk` | `godseye_nav` | `street_walk` | B | argument walk is start, pause, resume, step, stop, or state |
| `godseye_street_level` | `godseye_nav` | `street_level` | B | argument street is at, next, prev, or route |
| `godseye_itinerary` | `godseye_nav` | `itinerary` | B | drive or walk through pins; exact route id before a fuzzy label |
| `godseye_live_compare` | `godseye_nav` | `compare` | B | Wayback or imagery swipe |
| `godseye_timeline` | `godseye_nav` | `timeline` | B | argument timeline is set, play, step, stop, state, or list_dates |
| `godseye_live_track_entity` | `godseye_track` | `track` | B | follow one entity |
| `godseye_live_stop_tracking` | `godseye_track` | `stop` | B | stop following |
| `godseye_live_select_nearest_aircraft` | `godseye_track` | `select_nearest` | B | fly, enable flights, select the nearest airborne aircraft |
| `godseye_live_frame_overhead` | `godseye_track` | `frame` | B | frame nearby aircraft, ships, or satellites; not the street/rooftop/district framing argument |
| `godseye_live_control_cockpit` | `godseye_track` | `cockpit` | B | ride a selected aircraft; a track action, not a layer |
| `godseye_investigation_save` | `godseye_scene` | `save` | B | save the current scene |
| `godseye_investigation_list` | `godseye_scene` | `list` | B | list saved investigations |
| `godseye_investigation_load` | `godseye_scene` | `load` | B | load an investigation |
| `godseye_investigation_delete` | `godseye_scene` | `delete` | B | delete requires confirm=true |
| `godseye_investigation_report` | `godseye_scene` | `report` | B | HTML and Markdown report |
| `godseye_investigation_export` | `godseye_scene` | `export` | B | GeoJSON and KML, optional pack, folded into scene |
| `godseye_live_restore_scene` | `godseye_scene` | `restore` | B | re-apply this chat's scene |
| `godseye_live_run_tour` | `godseye_scene` | `tour` | B | start a guided tour |
| `godseye_live_tour_status` | `godseye_scene` | `tour_status` | B | poll a tour job |
| `godseye_live_stop_tour` | `godseye_scene` | `tour_stop` | B | cancel a tour |
| `godseye_live_control_scene` | `godseye_scene` | `playback` | B | cinematic scene playback; not an investigation |
| `godseye_watch_create` | `godseye_watch` | `create` | B | only when the user asks |
| `godseye_watch_list` | `godseye_watch` | `list` | B | check=<id> runs one watch now; there is no separate check tool |
| `godseye_watch_pause` | `godseye_watch` | `pause` | B | pause or resume |
| `godseye_watch_delete` | `godseye_watch` | `delete` | B | requires confirm=true |
| `godseye_live_set_hud` | `godseye_capture` | `hud` | B | HUD visibility and layout |
| `godseye_hud_summary` | `godseye_capture` | `summary` | B | Hermes-model caption; the handler closes over ctx.llm, so it is not a live or MCP call |
| `godseye_pane_shot` | `godseye_capture` | `pane` | B | PNG of the pane, including the drawer, pins, and imagery |
| `godseye_live_get_current_view_state` | `godseye_view` | `state` | B | folded into view; the per-turn map line is the orientation path |
| `godseye_doctor` | `godseye_admin` | `doctor` | B | health check; probe=true or detail=full calls each keyed provider once |
| `godseye_batch` | `godseye_admin` | `batch` | B | calls is a list of {tool, args}; stop_on_error stops early |
| `godseye_keys` | `godseye_admin` | `keys` | B | action install reads a KEY=VALUE path; values are never echoed |
| `godseye_rebuild_panel` | `godseye_admin` | `rebuild` | B | rebuild dist/panel |
| `godseye_live_reload_pane` | `godseye_admin` | `reload` | B | reload the open pane |
| `godseye_preview_swap` | `godseye_admin` | `preview` | B | single desktop preview tab |
| `godseye_control_status` | `godseye_admin` | `status` | B | control room; read-only |
| `godseye_app_start` | `godseye_admin` | `app_start` | B | start the app; user-triggered |
| `godseye_app_stop` | `godseye_admin` | `app_stop` | B | stop the app after ownership checks |
| `godseye_app_restart` | `godseye_admin` | `app_restart` | B | restart the app; refuses a foreign port |

## Extension ops

New behaviour is a new op on one of the eleven tools, never a twelfth tool. A
plugin module named `gev/ext_<name>.py` exports `EXT_OPS`, a list of frozen
`ExtOp` records from `gev/ext_ops.py`:

```python
from gev.ext_ops import ExtOp

def handler(args, **kw):
    return '{"ok": true}'

EXT_OPS = [
    ExtOp(
        tool="godseye_nav",
        op="hello",
        handler=handler,
        props={"target": {"type": "string", "description": "Who to greet."}},
        blurb="Say hello without leaving the map.",
    ),
]
```

`props` maps an argument name to its JSON Schema property. A property marked
with `"required": True` is enforced by the router and the marker is stripped
before the schema reaches the model. `handler(args, **kw)` receives only the
declared props; `session_id` and the other keyword arguments are forwarded.

`gev/ext_ops.discover()` imports every `gev/ext_*.py` module on disk and any
matching module already in `sys.modules`. A module that fails to import is
skipped, and its error is kept by `gev/ext_ops.errors()` for the doctor. An op
that names a tool outside the eleven, or that duplicates an existing
`(tool, op)` pair (including two extension modules claiming the same pair),
raises `ValueError` during discovery. The router reads the discovered records
for the op enum, the schema union, the op blurb, `prepare`, and dispatch, so a
new extension module needs no router edit. `register_tools` still registers
exactly the eleven composable tools; extension ops are reached through them.

The `godseye_admin` `op=doctor` report includes `catalog_age`, which reads
`generated_at` from `gev/feature_snapshot.json` (ok under 30 days, warn
otherwise) and points at `scripts/snapshot_tools.sh`. It also reports any
extension module that failed to import.
