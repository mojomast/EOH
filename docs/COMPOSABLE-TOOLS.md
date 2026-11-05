# Composable tools

Phase A of the tool consolidation. The model catalog registers `godseye_view` and `godseye_query`. Both take a required `op` and call the same handlers the old names used (`make_live_handler`, `make_handler`, or `extra_handler` in `gev/hermes_tools.py`). The 18 names those ops replaced are not registered and are not listed in `plugin.yaml` `provides_tools`.

`router.dispatch_legacy(name, args)` is the internal map. It accepts one of those retired Hermes names and runs the op's handler with the original arguments. Saved scenes, tours, `/gev`, the relay, and the pane scripts do not use Hermes tool names. They keep the bare pane actions (`fly_to_location`, `move_camera`, `get_current_view_state`, `hermes_context`). Those paths are unchanged.

Tools whose composable home is not registered yet stay registered under their current names. `godseye_investigation_export` is one of them. It is not folded into `godseye_view` or `godseye_query`.

The other ten composable tools in the target set (`godseye_layers`, `godseye_feeds`, `godseye_annotate`, `godseye_nav`, `godseye_track`, `godseye_scene`, `godseye_watch`, `godseye_capture`, `godseye_state`, `godseye_admin`) are mapped below and are not implemented in this change.

Plugin id stays `godseye`. New tool names keep the `godseye_` prefix.

## What phase A actually runs

| Tool | Ops |
|---|---|
| `godseye_view` | `fly`, `move`, `zoom`, `globe`, `home`, `share`, `panes` |
| `godseye_query` | `find_places`, `osm`, `context`, `what_is_here`, `brief`, `brief_and_show`, `analyst`, `military`, `poi`, `resolve_area`, `entity` |

`op=fly` accepts `style`. `nvg`, `night`, `night vision`, and `night-vision` mean `surveillance`. `flir`, `ir`, and `infrared` mean `thermal`. After a successful fly the router calls the existing `set_visual_style` handler. A failed fly does not change the style. `godseye_live_set_visual_style` stays a live tool until `godseye_layers` exists.

`here=true` on `find_places`, `brief`, `brief_and_show`, `military`, and `what_is_here` reads the open pane with `get_current_view_state` and then calls the original handler. A query or `near` value of `me`, `here`, or `this area` does the same. `find_places` then searches within 1500 m unless `radiusM` is set. `brief` uses the camera range (clamped to 1–100 km, or 25 km when the range is unknown) as `radius_km`.

The composable entry applies the same person-tracking refusal as the rest of the plugin, including strings nested inside `area` or `location`. It refuses before any pane or network call.

## Adjustments from the research table

The starting table is in the 2026-11-05 research note, section 1. These rows differ because of the handlers in this tree:

- **Framing** is the `framing` argument on `fly` (`street`, `rooftop`, `district`), not an op. `godseye_live_frame_overhead` stays on `godseye_track` (`op=frame`): it frames nearby aircraft, ships, or satellites.
- **`zoom_to_globe` is `op=globe`**, not `zoom`. The live tool's own description says a relative zoom cannot reach the whole Earth.
- **`fly_route` stays on `godseye_nav`.** It dollies along a route that was already drawn. It is not a camera fly.
- **`resolve_area`, `poi_details`, and `get_entity_context` are query ops.** `resolve_area` is how `analyst` and `osm` get an `areaId`. `poi_details` is the one-venue step after `find_places`. Entity context is the "entity" role in the research note.
- **`get_terrain_height` and `get_map_features` are query ops, phase later.** They are point lookups, not feeds. They are not the same call as `what_is_here`, so they stay registered.
- **`show_in_gods_eye_view` is `godseye_view` `op=show`, phase later.** The payload is a composed scene (area, layers, style, marks, follow). Putting that schema on `godseye_view` now would undo the size cut. `fly` plus `style` covers "fly to Tokyo NVG".
- **Panels, Contacts mode, sonar, and detection sit with layers.** They change pane chrome or a visual overlay. Cockpit sits with track. Cinematic `control_scene` sits with scene (`op=playback`), separate from investigation save/load. CCTV control and radio sit with feeds.
- **Two satellite-pass tools.** `godseye_next_satellite_pass` is the MCP call (`op=next_pass`, no pane). `godseye_live_next_satellite_pass` is the pane catalog (`op=satellite_pass`). `godseye_live_next_iss_pass` is `op=iss_pass`.
- **`godseye_measure` already has an `op` of `distance`, `area`, or `route`.** When `godseye_nav` lands, that value moves to an argument named `measure` so it does not collide with the router `op`.
- **Watch check is not a tool.** `godseye_watch_list` takes `check=<id>`.
- **`get_current_view_state` stays `godseye_state` `op=get`.** The per-turn EOH map line is the orientation path. The router uses this call only to resolve `here=true`.
- **`godseye_hud_summary` is `godseye_capture` `op=summary`.** Its handler closes over `ctx.llm`, so it is not a live or MCP dispatch. Screenshot capture inside `investigation_report` is not its own tool.

## Selection eval

`evals/selection_golden.json` is a fixed set of natural-language tasks with the expected tool, `op`, and argument shape. `tests/test_composable.py` checks schemas, router dispatch, alias parity, and that golden file offline. `evals/run_selection.py` sends the same set to a live model when `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set. It exits 0 without calling a model when no key is set, and whenever `CI` or `GITHUB_ACTIONS` is set. `evals/measure_schemas.py` prints schema bytes and token counts (tiktoken `o200k_base` and `cl100k_base` when installed). The baseline it reconstructs is the registered schemas with the two new tools removed and the deprecation prefix stripped.

## Full map

Phase `A` is implemented. Phase `later` is the target only.

| Legacy tool | Composable | op | Phase | Notes |
|---|---|---|---|---|
| `godseye_live_fly_to_location` | `godseye_view` | `fly` | A | framing is an argument on fly (street, rooftop, district), not its own op |
| `godseye_live_move_camera` | `godseye_view` | `move` | A | orbit, pan, tilt, rotate, rotate_globe, or stop |
| `godseye_live_adjust_camera_zoom` | `godseye_view` | `zoom` | A | relative step only; whole-Earth is op=globe |
| `godseye_live_zoom_to_globe` | `godseye_view` | `globe` | A | separate op because a relative zoom cannot reach the globe |
| `godseye_home` | `godseye_view` | `home` | A | action stays go, set, or get |
| `godseye_share_link` | `godseye_view` | `share` | A | share link for the open view |
| `godseye_live_panes` | `godseye_view` | `panes` | A | list, label, or prune pane records |
| `godseye_show_in_gods_eye_view` | `godseye_view` | `show` | later | composed scene (area, layers, style, marks, follow); left off phase A so godseye_view stays a camera tool |
| `godseye_live_set_layer_visibility` | `godseye_layers` | `visibility` | later | one layer on or off |
| `godseye_live_show_data_layers_menu` | `godseye_layers` | `menu` | later | open the layers menu |
| `godseye_live_set_panel_open` | `godseye_layers` | `panel` | later | side panels are chrome, grouped with the menus |
| `godseye_live_set_context_mode` | `godseye_layers` | `context_mode` | later | Contacts or Space Missions mode, not a data query |
| `godseye_live_set_visual_style` | `godseye_layers` | `style` | later | home of style; phase A fly accepts style and calls this handler, so this name is not deprecated yet |
| `godseye_live_set_cyber_sonar` | `godseye_layers` | `sonar` | later | HUD decoration, not a feed |
| `godseye_live_set_detection` | `godseye_layers` | `detection` | later | detection overlay density |
| `godseye_live_set_map_stack` | `godseye_layers` | `stack` | later | basemap stack |
| `godseye_live_set_post_processing` | `godseye_layers` | `post` | later | post-processing toggles |
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
| `godseye_get_terrain_height` | `godseye_query` | `terrain` | later | point elevation, not a streaming feed; not in the phase A query set |
| `godseye_get_map_features` | `godseye_query` | `features` | later | named OSM features at a point, sibling of what_is_here; phase B |
| `godseye_get_earthquakes` | `godseye_feeds` | `earthquakes` | later | USGS events |
| `godseye_get_active_fires` | `godseye_feeds` | `fires` | later | NASA FIRMS hotspots |
| `godseye_get_recent_launches` | `godseye_feeds` | `launches` | later | recent rocket launches |
| `godseye_aircraft_in_area` | `godseye_feeds` | `aircraft_in_area` | later | aircraft in an area |
| `godseye_find_aircraft` | `godseye_feeds` | `find_aircraft` | later | aircraft lookup |
| `godseye_get_aircraft_track` | `godseye_feeds` | `aircraft_track` | later | one aircraft track |
| `godseye_get_aircraft_info` | `godseye_feeds` | `aircraft_info` | later | one aircraft record |
| `godseye_vessels_in_area` | `godseye_feeds` | `vessels_in_area` | later | ships in an area |
| `godseye_find_vessel` | `godseye_feeds` | `find_vessel` | later | ship lookup |
| `godseye_get_vessel_track` | `godseye_feeds` | `vessel_track` | later | one ship track |
| `godseye_next_satellite_pass` | `godseye_feeds` | `next_pass` | later | MCP pass prediction; no pane. The live tool is op=satellite_pass |
| `godseye_satellites_overhead` | `godseye_feeds` | `overhead` | later | satellites above a point |
| `godseye_find_cctv_cameras` | `godseye_feeds` | `find_cctv` | later | public CCTV camera search |
| `godseye_get_cctv_snapshot` | `godseye_feeds` | `cctv_snapshot` | later | one camera snapshot |
| `godseye_find_alpr_cameras` | `godseye_feeds` | `alpr` | later | ALPR camera search |
| `godseye_get_traffic_flow` | `godseye_feeds` | `traffic` | later | traffic flow |
| `godseye_get_weather_map` | `godseye_feeds` | `weather` | later | weather raster |
| `godseye_get_recent_imagery` | `godseye_feeds` | `imagery` | later | MCP recent imagery. The pane tool is op=imagery_live |
| `godseye_find_submarine_cables` | `godseye_feeds` | `cables` | later | submarine cables |
| `godseye_find_infrastructure` | `godseye_feeds` | `infrastructure` | later | mapped infrastructure |
| `godseye_get_bhote_koshi_flood` | `godseye_feeds` | `flood` | later | single flood event, not a general query |
| `godseye_get_cyclones` | `godseye_feeds` | `cyclones` | later | tropical cyclones |
| `godseye_get_fire_perimeters` | `godseye_feeds` | `fire_perimeters` | later | wildfire perimeters |
| `godseye_find_military_installations` | `godseye_feeds` | `military_sites` | later | OSM installation catalog; the brief is godseye_query op=military |
| `godseye_live_find_imagery` | `godseye_feeds` | `imagery_live` | later | pane recent-imagery search |
| `godseye_live_next_iss_pass` | `godseye_feeds` | `iss_pass` | later | next ISS pass from the pane |
| `godseye_live_next_satellite_pass` | `godseye_feeds` | `satellite_pass` | later | next pass from the loaded pane catalog |
| `godseye_live_control_cctv` | `godseye_feeds` | `cctv_control` | later | pane CCTV controls; snapshots stay op=cctv_snapshot |
| `godseye_live_control_radio` | `godseye_feeds` | `radio` | later | internet radio playback |
| `godseye_live_annotate_map` | `godseye_annotate` | `annotate` | later | pins, routes, cards |
| `godseye_live_clear_annotations` | `godseye_annotate` | `clear` | later | clear marks on the pane |
| `godseye_live_draw` | `godseye_annotate` | `draw` | later | user draw mode or Hermes shapes |
| `godseye_live_list_annotations` | `godseye_annotate` | `list` | later | stored marks for this chat |
| `godseye_live_delete_annotation` | `godseye_annotate` | `delete` | later | delete one stored mark |
| `godseye_live_filter_pins` | `godseye_annotate` | `filter` | later | category, rating, open-now chips |
| `godseye_live_pin_drawer` | `godseye_annotate` | `drawer` | later | open or close one pin drawer |
| `godseye_research_pins` | `godseye_annotate` | `research` | later | fill drawers from free sources |
| `godseye_measure` | `godseye_nav` | `measure` | later | inner distance, area, or route moves to argument measure when this tool exists, so it does not collide with op |
| `godseye_isochrone` | `godseye_nav` | `isochrone` | later | minutes of reach |
| `godseye_live_fly_route` | `godseye_nav` | `fly_route` | later | dolly along an existing route; not a camera fly |
| `godseye_live_compare` | `godseye_nav` | `compare` | later | Wayback or imagery swipe |
| `godseye_live_track_entity` | `godseye_track` | `track` | later | follow one entity |
| `godseye_live_stop_tracking` | `godseye_track` | `stop` | later | stop following |
| `godseye_live_select_nearest_aircraft` | `godseye_track` | `select_nearest` | later | fly, enable flights, select the nearest airborne aircraft |
| `godseye_live_frame_overhead` | `godseye_track` | `frame` | later | frame nearby aircraft, ships, or satellites; not the street/rooftop/district framing argument |
| `godseye_live_control_cockpit` | `godseye_track` | `cockpit` | later | ride a selected aircraft; a track action, not a layer |
| `godseye_investigation_save` | `godseye_scene` | `save` | later | save the current scene |
| `godseye_investigation_list` | `godseye_scene` | `list` | later | list saved investigations |
| `godseye_investigation_load` | `godseye_scene` | `load` | later | load an investigation |
| `godseye_investigation_delete` | `godseye_scene` | `delete` | later | delete requires confirm=true |
| `godseye_investigation_report` | `godseye_scene` | `report` | later | HTML and Markdown report |
| `godseye_investigation_export` | `godseye_scene` | `export` | later | GeoJSON and KML, optional pack; not folded into view or query |
| `godseye_live_restore_scene` | `godseye_scene` | `restore` | later | re-apply this chat's scene |
| `godseye_live_run_tour` | `godseye_scene` | `tour` | later | start a guided tour |
| `godseye_live_tour_status` | `godseye_scene` | `tour_status` | later | poll a tour job |
| `godseye_live_stop_tour` | `godseye_scene` | `tour_stop` | later | cancel a tour |
| `godseye_live_control_scene` | `godseye_scene` | `playback` | later | cinematic scene playback; not an investigation |
| `godseye_watch_create` | `godseye_watch` | `create` | later | only when the user asks |
| `godseye_watch_list` | `godseye_watch` | `list` | later | check=<id> runs one watch now; there is no separate check tool |
| `godseye_watch_pause` | `godseye_watch` | `pause` | later | pause or resume |
| `godseye_watch_delete` | `godseye_watch` | `delete` | later | requires confirm=true |
| `godseye_live_set_hud` | `godseye_capture` | `hud` | later | HUD visibility and layout |
| `godseye_hud_summary` | `godseye_capture` | `summary` | later | Hermes-model caption; the handler closes over ctx.llm, so it is not a live or MCP call |
| `godseye_live_get_current_view_state` | `godseye_state` | `get` | later | full view read; prefer the per-turn EOH map line to orient |
| `godseye_doctor` | `godseye_admin` | `doctor` | later | read-only health check |
| `godseye_rebuild_panel` | `godseye_admin` | `rebuild` | later | rebuild dist/panel |
| `godseye_live_reload_pane` | `godseye_admin` | `reload` | later | reload the open pane |
| `godseye_preview_swap` | `godseye_admin` | `preview` | later | single desktop preview tab |
