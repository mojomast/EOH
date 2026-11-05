# Changelog

## 0.2.0 (unreleased)

### Changed
- Renamed the user-facing title to **Eye Of Hermes (EOH)** (pane title, plugin description, `/gev` help, skill, README). Plugin id `godseye`, tool names, `/gev`, routes and repo name are unchanged.

### Added
- Pin clustering and filters (pane side, `gev/panel_pins.js` + vendored opening_hours subset `gev/panel_oh.js`). Above ~2.5 km camera height, pins within 56 px merge into a count bubble; clicking one zooms to its members. A chip bar filters by category (amenity / shop / tourism / leisure / cuisine / category tags), rating >= 4 (rating or stars tags) and "Open now" (OSM opening_hours; unsupported syntax such as PH, months, sunrise is UNKNOWN, never guessed; unknown stays visible unless `include_unknown:false`). New annotation field `tags` (filled by `godseye_find_places`). New tool `godseye_live_filter_pins` (set / state / expand). Filters, including chip clicks (new `/live/filters` route), persist in the scene and are re-applied after a restore. Pins stay grounded and the drawer shows rating and open state.
- Area watches: `godseye_watch_create`, `godseye_watch_list` (with `check=<id>` to run one now), `godseye_watch_pause`, `godseye_watch_delete` (needs `confirm:true`). Area is a bbox, polygon, center + radius or the current view. Feeds: earthquakes (USGS, keyless), fires (NASA FIRMS, `FIRMS_MAP_KEY`), flights (OpenSky, keyless), ships (`AISSTREAM_API_KEY` + the app's `vessels_in_area`), pois (OSM). Feeds without a key report `KEY_MISSING`. The first run is a baseline by default. After that, only unseen ids alert; last-seen ids persist in `plugin-data/godseye/watches.json`. Each new item is pinned with drawer details. Alerts are queued and handed to the next chat turn through the `pre_llm_call` hook. `deliver:"inject"` also calls `ctx.inject_message`, which needs `allow_gateway_injection`. The checks run in a daemon thread that exists only while an active watch exists. Nothing is created automatically.
- Grounded pins: annotation/POI/brief/tour pins are drawn as Cesium markers anchored at their tip (verticalOrigin BOTTOM) with a ground dot, depth test disabled and the ground height re-sampled per zoom level. Fixes pins hovering above the spot at street level (upstream's screen-space reticle samples the height once, usually at a coarse zoomed-out tile LOD, and never re-samples). Upstream's reticle for those pins is hidden; ids, list and clear are unchanged.
- Pin info drawer: click a pin (or `godseye_live_pin_drawer`) for a slide-out with title, sanitized markdown, lazy images and http(s) links (opened by the host via `ui/open-link`). New optional annotation fields `title`, `description_md`, `images[]`, `links[]`; `godseye_find_places` fills them from OSM tags, `godseye_brief_and_show` from hits (`pin_details` overrides). The open drawer persists with the scene and is restored.
- `godseye_find_places`: keyless POI search ("ramen restaurants in Montreal"). Category/cuisine-in-bbox via Overpass (configured endpoint first, then overpass.kumi.systems, overpass.private.coffee, overpass-api.de; 20 s per request, 45 s total, per-endpoint circuit breaker), name/address lookup and a keyword fallback via Nominatim (descriptive User-Agent, 1.2 s pacing, 1 h cache). Returns lat/lon, name, address, website, opening hours; `pin:true` pins up to 24 via annotate_map.
- `godseye_poi_details` (best effort): OSM record, website, menu URL if linked, and verbatim price snippets from the venue's site. Prices are never invented.
- `resolve_area` falls back to `around:true` (buffer polygon) for point-only places, then to a Nominatim bbox polygon.
- `AREA_TOO_LARGE` remediation says to fly with rangeM <= 15000 first. `OVERPASS_NOT_CONFIGURED` explains the public fallback and gives the exact `OVERPASS_UPSTREAMS=` line.
- `godseye_brief_and_show`: runs situation_brief or military_awareness, then flies the live globe, enables the brief's layers, pins the top N hits and sets a caption line. Returns the brief plus what was shown, and still returns the brief when no pane is open. If upstream geocoding is unavailable, it retries with coordinates.
- Multi-pane live relay. Panes register an id and their chat session ids, and commands go to the session's pane, else the most recently active ready pane. Every live tool takes an opt-in `fanout`. New `godseye_live_panes` tool and `/live/panes` route. `pane_busy` is now distinct from `no_pane`.
- `/gev` slash command (`fly`, `layers`, `status`, `brief`, `home [set]`, `share`, `doctor`) via `ctx.register_command`.
- Bundled skill `godseye:godseye-analyst` (analyst playbook) via `ctx.register_skill`.
- `godseye_doctor` health check with fix text (Node 24, checkout, :4173, patches #1571/#1572, relay, panes and last-poll age). It is read-only.
- Saved home view (`godseye_home`, `/gev home`) in `ctx.state` with a `plugin-data/godseye/home.json` mirror. The pane cold-starts at home (`/home` route).
- `godseye_share_link`: upstream share-codec `/share/<code>` link on `GODSEYE_PUBLIC_URL`, with a camera-hash fallback.
- In-frame caption line (`hermes_caption`) over the globe.
- Scene persistence: annotations (stored with resolved coordinates, so a restore never re-geocodes), camera (look-at target, range, pitch and heading) and caption are saved per session in `plugin-data/godseye/scenes.json`. A reopened pane restores them automatically. New tools: `godseye_live_list_annotations`, `godseye_live_delete_annotation`, `godseye_live_restore_scene`, `godseye_live_reload_pane`.
- Guided tours: `godseye_live_run_tour` (street/rooftop/district framing with exact pitch, optional orbit, dwell, optional route between stops) runs server-side as one job, with `godseye_live_tour_status` and `godseye_live_stop_tour`. A tour always ends with the camera stopped.
- Doctor panel checks: `PANEL_MISSING`, `PANEL_STALE`, `KEY_MISSING`, `ION_SCOPES` (local JWT decode, values never shown), plus the active map stack and the scene annotation count. `godseye_rebuild_panel` rebuilds `dist/panel` from `~/.config/gods-eye-view.env` and reloads open panes. `set_map_stack` failures include remediation text.
- Doctor now reports per-provider key presence (`CESIUM_ION_TOKEN`, `GOOGLE_MAPS_API_KEY`, `MAPILLARY_CLIENT_TOKEN` in env+bundle; `AISSTREAM_API_KEY`, `FIRMS_MAP_KEY` in env), the active map stack, layer feed states from `feedProvenance` (nominal/fallback/unavailable), and the current scene's annotation count. Key values are never printed.
- Pane bookkeeping: stable pane id per chat session, one pane per session by default, auto-prune of stale closed records plus `godseye_live_panes {prune:true}`, optional pane `label` (shown in the list and pane chrome).
- `godseye_preview_swap`: tracks the last desktop preview URL this plugin opened and closes it before opening the next. `desktop_preview` remains a Hermes core tool (no native swap action); when the desktop emitter is unavailable the tool returns `desktop_preview_steps` for the agent.
- Brief captions carry a data source and a count scope (`queried extent` / `in view`), never "worldwide".
- View state now returns pitch, heading, look-at target and range (pane-side shim).
- `fly_to_location` takes `framing: street|rooftop|district` (150 m/-15°, 450 m/-30°, 2500 m/-45°; `rangeM` overrides, minimum 100 m). It waits for arrival, then sets the pitch exactly, and returns pitchDeg/headingDeg with the camera.
- Annotations: multi-line labels (newline string or list, 2-3 lines) and a `card` type (title plus rows `[{text, price}]`, anchored to a point) drawn as callouts over the globe. Partial routes draw the resolvable runs of waypoints and name the missing one(s) in `missingWaypoints`/`failedLabels`; they never bridge a gap. `flyTo: "all"` frames every mark in the call.

### Fixed (tours)
- The final tour stop could end at the flight's default -35° instead of the framing pitch. The cause was an unfinished flight or a running orbit overwriting the exact framing. Framing now cancels any in-flight flight, the orbit is stopped before each next flight, and the pose is verified and re-framed (up to 3 tries) until the pitch holds.

### Fixed
- Resizing the window could crash the globe, ending in "Error constructing CesiumWidget: The browser supports WebGL, but initialization failed". The cause was the pane being remounted when the layout changed. Each remount started a new Cesium viewer while the old frame's WebGL context was freed only at GC, so contexts leaked until the browser refused new ones. v0.1.0 had the same problem. The pane is now `lifecycleKeepAlive`, the panel document is cached across remounts, and the frame releases its context on `pagehide`/dispose (`WEBGL_lose_context` + `viewer.destroy()`). Context loss and Cesium's error dialog now show a "click to reload the globe" card instead of a dead dialog.

### Changed
- The live relay no longer hands a command to whichever pane polls first.
- Version 0.2.0. Every new Hermes API is optional: older builds skip the command, skill or state and still register the tools.

## 0.1.0
- First release: 29 data tools, 33 live-globe tools, Hermes HUD summaries, and the Desktop/Webapp pane.
