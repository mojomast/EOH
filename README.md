<div align="center">

# Eye Of Hermes

**A live 3D globe in the Hermes pane, which the agent can fly, measure, and watch.**

Eye Of Hermes (EOH) is the Hermes plugin `godseye` (version 0.2.0). It runs a [fork of God's Eye View](https://github.com/mojomast/gods-eye-view) beside the chat and gives Hermes the tools to drive it. Answers come from your Hermes model.

[Tour](#a-tour-of-the-pane) · [How it works](#how-it-works) · [Quick start](#quick-start) · [Tools](#tools) · [Privacy](#privacy-and-responsible-use)

<img src="docs/images/eoh-header.jpg" alt="Eye Of Hermes header over photorealistic 3D tiles of Montreal, with two ramen pins and an Open full app button" width="880">

<sub>The Eye Of Hermes header on photorealistic 3D tiles of Montreal. Style is Normal. Two ramen pins sit on the city, and <b>Open full app</b> leaves the pane for the full God's Eye View window.</sub>

</div>

---

## What it is for

Hermes can talk about a place. A list of coordinates is a poor way to show one. [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view), by Bilawal Sidhu, already has the globe: public aircraft, ships, satellites, earthquakes, fires, weather, and a photorealistic city. Eye Of Hermes puts that globe in a Hermes Desktop or Webapp pane and registers its actions as tools, so the same chat that answers a question can move the camera, pin what it found, and leave a watch running.

The live commands go through God's Eye View's own voice-action runner. There is one globe. The agent drives the same path the app already uses for voice.

A few things make that usable in a long chat:

- Before each model turn, Hermes can receive one short map line: whether the pane is connected, busy, or closed, plus camera center, range, pitch, heading, active layers, pin count, and the open drawer. The line is built from the relay's cached snapshot. It does not call the pane, so a busy globe is left alone. `GODSEYE_TURN_STATE=0` drops the line. Hosts without `pre_llm_call` skip it, and the tools still work.
- Large tool results default to ids and counts. Pass `detail=full` when you need the rows, the geometry, or an image.
- A failed call comes back as `code`, `message`, and `fix`. The fix is the next action (open the pane, retry the same call, rebuild the panel), and the agent is told to follow it.
- The pane is kept alive across layout changes, so resizing the window does not construct a new WebGL context on every move. On the fork, a hidden pane pauses the viewer.

Person search, face recognition, and tracking an individual are refused in `gev/policy.py` before a call reaches the app. Following an aircraft, ship, or satellite by its public identifier is a normal tool.

## A tour of the pane

The pictures are the running pane over Montreal. The names and coordinates are public places. Imagery credits in the corner (Cesium ion, Esri) belong to those providers, through God's Eye View.

### What am I looking at

`godseye_live_context` reads the camera, the visible box, the selected pin, the last clicked ground point, and any shape on the map. `focus` is its best guess for "this": the open pin, then the newest drawing, then the last click, then the centre of the screen. It reverse-geocodes that point and lists nearby named places. `godseye_what_is_here` does the same lookup for a lat/lon with no pane open.

The toolbar at the bottom left draws a box (two clicks), an area, or a line. Distance and area are labeled on the shape. **Ask Hermes** copies a ready question to the clipboard and focuses the chat composer. The Desktop plugin SDK cannot submit a chat message on its own, so you paste and send.

<img src="docs/images/eoh-context.jpg" alt="Marché Bonsecours pin drawer beside a drawn box labeled 4.91 hectares, with Box, Area, Line, and Ask Hermes" width="880">

<sub>A drawn box of 4.91 ha around Marché Bonsecours. The drawer shows 45.50886, -73.55180 and "Bonsecours Market, 1847, Old Montréal." <b>Ask Hermes</b> is on the drawer and on the draw toolbar.</sub>

### Measure, routes, and reach

`godseye_measure` returns great-circle distance (each leg, with bearings), the area and perimeter of a polygon or box, or a walk, bike, or drive route. Routes use the public OSRM server. `godseye_isochrone` draws reach rings from the public Valhalla server. If Valhalla is down, one OSRM `table` call (16 bearings, 4 distances) builds a polygon and marks it `approximate`. If both are down, the tool returns `routing_unavailable`. It does not substitute a straight-line circle.

<img src="docs/images/eoh-measure.jpg" alt="Five- and ten-minute walk isochrones, a 4.91 hectare box, and a 637 metre label around Marché Bonsecours and Basilique Notre-Dame" width="880">

<sub>Walking reach from the market: 5- and 10-minute isochrones, the 4.91 ha box, and a 637 m measurement, with pins on Marché Bonsecours and Basilique Notre-Dame.</sub>

### Before and after

`godseye_live_compare` splits the view. Each side is `current`, `osm`, or `wayback:YYYY[-MM[-DD]]` (Esri World Imagery Wayback, a keyless WMTS). A date selects the release on or before that day. `action=list_dates` lists releases. God's Eye View shows one map stack at a time, so two stacks cannot be split, and photorealistic 3D tiles are hidden while the swipe is up, because they draw above the imagery. Wayback dates are release dates; the photo under a spot can be older than the label.

<img src="docs/images/eoh-compare.jpg" alt="Swipe compare with Esri Wayback 2014-12-30 on the left and the current view on the right, pins still visible" width="880">

<sub>Left: <b>Esri Wayback 2014-12-30</b>. Right: <b>Current view</b>. The pins stay on Marché Bonsecours and Basilique Notre-Dame. Drag the divider to move the split.</sub>

### Also on the globe

- **Pins.** Annotation pins, place pins, brief hits, and tour stops are ground-clamped markers. The tip is the point, a dot sits on the ground under it, and the height is sampled again as the camera's level of detail changes. Click a pin, or call `godseye_live_pin_drawer`, for a drawer of title, `description_md` (markdown; raw HTML is shown as text), images, and http(s) links. The open drawer is saved with the scene.
- **Clusters and filters.** Above a camera height of 2,500 m, pins within 56 screen pixels merge into a count bubble. Click it, or `godseye_live_filter_pins` with `action=expand`, to zoom in. A chip bar filters by category (OSM `amenity`, `shop`, `tourism`, `leisure`, `cuisine`), by rating, and by **Open now**. Open now understands a subset of OSM `opening_hours`: `24/7`, weekday ranges and lists, overnight spans, and `off`, with a later rule replacing earlier ones. Anything else is unknown, and unknown pins stay visible unless `include_unknown` is false. The clock is the pane's local time.
- **Research and investigations.** `godseye_research_pins` fills drawers from Wikipedia, Wikidata, Wikimedia Commons, OSM tags already on the pin, and a site's meta description. Missing sources are marked unavailable. `godseye_investigation_save` / `_load` / `_list` / `_delete` store a named scene. `godseye_investigation_report` writes HTML and Markdown under `plugin-data/godseye/reports/`, with a map image when the pane can read its canvas. `godseye_investigation_export` writes that investigation, or the current scene when `name` is omitted, under `plugin-data/godseye/exports/`. `format` is `geojson`, `kml`, `both` (the default), or `pack`. GeoJSON is RFC 7946 with longitude then latitude. Pins are points and carry their drawer and enrichment properties. Drawings and areas are polygons or lines. Routes are lines. `pack` is a zip of the GeoJSON, the KML, the Markdown report, and any saved screenshots. Google photorealistic tile imagery is left out of the files. `/gev export` runs the same tool.
- **Area watches.** `godseye_watch_create` polls an area for earthquakes (USGS), fires (NASA FIRMS), flights (OpenSky), ships (through the app), or OSM places. The first run records what is already there. Later runs alert only on ids that have not been seen, pin the new items, and queue a message for the next chat turn. With `deliver=inject`, the plugin also calls `ctx.inject_message` when Hermes is configured to allow it.
- **Brief and show, home, share, tours.** `godseye_brief_and_show` runs a situation brief (or military awareness when the question is about military activity), flies there, turns on the brief's layers, and pins the top hits. `godseye_home` saves a view and opens a cold pane there. `godseye_share_link` builds a `/share/<code>` link with God's Eye View's share codec, or a camera-hash URL when that codec cannot run. `godseye_live_run_tour` flies a list of stops in the background.

## How it works

```mermaid
flowchart TB
  Agent["Hermes agent"] --> Hook["pre_llm_call"]
  Agent --> Tools["godseye tools"]
  Tools --> MCP["Data tools: node server/mcp/stdio.js"]
  MCP --> App["God's Eye View app"]
  Tools --> Relay["Live tools: loopback relay"]
  Pane["Hermes pane"] -->|"long-poll /live/next"| Relay
  Pane -->|"gev:config and gev:visibility"| Frame["Sandboxed iframe"]
  Pane -->|"hermes-gev:run"| Frame
  App -->|"panel document"| Pane
  Frame --> View["God's Eye View viewer"]
  Watch["Watch thread"] -->|"queued alerts"| Hook
  Watch -->|"inject_message, when allowed"| Agent
  Watch -->|"pin new items"| Relay
```

**Data tools** are a snapshot of God's Eye View's MCP catalog (`gev/tools_snapshot.json`). They call one persistent `node server/mcp/stdio.js` child. No pane is required. `panel_request` is in the upstream catalog and is deliberately not registered as a model tool.

**Live tools** are a snapshot of the app's realtime actions (`gev/live_tools_snapshot.json`), plus the tools this plugin adds (context, draw, measure, compare, pins, watches, investigations).

1. A live call is queued on a loopback relay, `127.0.0.1` port `GODSEYE_LIVE_PORT` (18792), with a random bearer token.
2. The open pane long-polls it through the plugin backend.
3. The pane posts `{type:'hermes-gev:run'}` into the iframe.
4. An injected script runs the action with `window.__godsEyeView.voiceCommands.runner`, the same runner voice uses, and the result comes back the same way.

A command no pane picks up within about 8 seconds returns `no_pane`. A pane still working on an earlier command returns `pane_busy`. Each pane keeps a stable id per chat session. A call goes to that pane, or to the most recently active one. `fanout: true` sends it to every open pane.

**Turn state and watches.** Hermes keeps one callback per hook name, so the map line and watch alerts share `pre_llm_call`. The text is the line, the alerts, or both, separated by a blank line. The line is at most 300 bytes and drops anything that looks like a key or a URL. A connected line has this shape (the numbers are an example, not a captured turn):

```text
EOH pane=connected cam=45.5089,-73.5518 r=800m p=-35.0 h=20.0 layers=flights pins=2 drawer=Marché Bonsecours
```

A closed pane is `EOH pane=closed`. Alerts look like `[EOH watch alert] <name>: <n> new item(s).`

Watches exist only when someone creates one. Hermes cron schedules model prompts, so the check itself runs in a daemon thread inside the Hermes process, and that thread runs only while a watch is active (`GODSEYE_WATCH_LOOP=0` stops it). Seen ids are stored, so a restart does not re-alert. `deliver=inject` also calls `ctx.inject_message` for that chat. Hermes accepts the injection when `plugins.entries.godseye.allow_gateway_injection` is true. Otherwise the alert stays queued for the next turn.

**Embed protocol.** A current God's Eye View build (the [mojomast fork](https://github.com/mojomast/gods-eye-view), documented in its `docs/EMBEDDING.md`) accepts two messages from the parent:

| Message | When the pane sends it | What the fork does |
| --- | --- | --- |
| `gev:config` | Before the viewer starts (`?waitForConfig=1`), with the client keys and `perf` | Applies keys and the performance profile, then replies `gev:config-applied` |
| `gev:visibility` | Page visibility and an intersection observer: hidden when the pane is covered or the tab is in the background | Pauses the render loop, HUD timers, and feed polls, then replies `gev:visibility-applied` |

The same keys are written to `dist/panel/config.json` in the God's Eye View checkout (mode 0600, gitignored there) and injected as `GEV_RUNTIME_CONFIG` in the panel HTML. `perf` is sent on the query string and in the message. It is not written into `config.json`. Changing a client key on a build that understands this protocol does not need `npm run build:panel`. Older builds still bake the keys; `godseye_rebuild_panel` is for those. Server keys (`AISSTREAM_API_KEY`, `FIRMS_MAP_KEY`) stay in the env file.

The pane frame is the Desktop SDK's `SandboxedFrame`: an opaque origin, scripts allowed, `allow-same-origin` never added. App files the panel needs are loaded through the plugin backend, and that backend allows only `panel_request` and `show_in_gods_eye_view` from the pane. The viewer then requests imagery from the providers configured in the app. A worker shim lets Cesium's workers run in the opaque frame. Branding (`gev/panel_brand.js`) replaces the app's header wordmark with **EOH** / **EYE OF HERMES** and leaves the provider credits alone.

A second loopback, the HUD bridge on `127.0.0.1:18791`, lets the app's own SUMMARY overlay call Hermes when patch `0001` is applied. `godseye_hud_summary` uses `ctx.llm` directly and does not need the bridge. Set `GODSEYE_HUD_BRIDGE=0` to skip the bridge. This plugin does not open the app's realtime voice session. Chat is the interface. The mic control in the pane belongs to God's Eye View, and it stays unused unless the app has its own key.

## Quick start

**You need**

- Hermes Agent, with Hermes Desktop or the Webapp for the pane.
- Node.js. God's Eye View documents 24.14 or later, or 26.x, and recommends 24.22.1 or later. This plugin's doctor checks that the major version is 24 or higher.
- A God's Eye View checkout. Use the [fork](https://github.com/mojomast/gods-eye-view) so the pane can send `gev:config`, `gev:visibility`, and `perf=embed`. The [upstream](https://github.com/bilawalsidhu/gods-eye-view) app still runs. Doctor reports when the HUD and full-UI patches below are missing.

```sh
# 1. The globe
git clone https://github.com/mojomast/gods-eye-view.git ~/gods-eye-view
cd ~/gods-eye-view && npm ci && npm run build:panel && npm run dev
# serves http://localhost:4173

# 2. The plugin
hermes plugins install mojomast/hermes-godseye
hermes plugins enable godseye
hermes plugins doctor godseye
```

The one-click install link is `hermes://plugin/install?repo=mojomast/hermes-godseye`.

Then enable **godseye** in Hermes Desktop or the Webapp, open the Eye Of Hermes pane, and start a new chat so the globe note is in the system prompt.

`scripts/install-service.sh` is optional. It installs the app as a user systemd service on `127.0.0.1:4173` and points it at the HUD bridge env file. The plugin never runs that script itself.

If doctor says the HUD or full-UI patch is missing, apply the opt-in patches in [`patches/`](patches) and rebuild the panel. Both leave upstream's default behavior unchanged when their env flags are unset.

| Patch | Upstream PR | What it unlocks |
| --- | --- | --- |
| `patches/0001-hud-summary-compatible-endpoint.patch` | [gods-eye-view#1571](https://github.com/bilawalsidhu/gods-eye-view/pull/1571) | The app's own HUD SUMMARY overlay, via `GEV_HUD_SUMMARY_BASE_URL` |
| `patches/0002-embed-full-ui.patch` | [gods-eye-view#1572](https://github.com/bilawalsidhu/gods-eye-view/pull/1572) | The full app UI in the pane, via `GEV_EMBED_FULL_UI` |

On a checkout without patch 0002 the pane shows the globe and hides the full-UI toggle. `godseye_hud_summary` still runs on Hermes. Live control and the data tools behave the same. Only the in-app SUMMARY overlay falls back to the app's own path.

### Environment

No key is stored in this repository. Put provider keys in `~/.config/gods-eye-view.env` (override the path with `GODSEYE_ENV_FILE`) and `chmod 600` the file. Names only. Doctor reports where a key is set and never prints the value.

**Provider keys**

| Name | Used for |
| --- | --- |
| `CESIUM_ION_TOKEN` | Photorealistic 3D tiles (Cesium ion asset 2275207, Google Photorealistic 3D Tiles). Doctor fails this check when it is missing, and reports `ION_SCOPES` when the token claims more than `assets:read` and `assets:list`. The token is shipped to the browser. |
| `GOOGLE_MAPS_API_KEY` | Google basemap and place labels for the HUD. Doctor allows it to be unset. |
| `MAPILLARY_CLIENT_TOKEN` | Mapillary street-level imagery. Doctor allows it to be unset. |
| `PHOTON_URL` | Photon geocoder. An empty value, or `off`, disables it. |
| `AISSTREAM_API_KEY` | Live ships, server-side only. Doctor reports `KEY_MISSING` when it is unset. |
| `FIRMS_MAP_KEY` | NASA FIRMS fires, server-side only. `FIRMS_API_KEY`, `FIRMS_KEY`, and `NASA_FIRMS_KEY` are accepted aliases. Doctor reports `KEY_MISSING` when it is unset. |

You can open the globe without the ship and fire keys. Doctor will still list them as missing, because those feeds cannot be checked.

**Plugin settings.** All optional.

| Name | Default | Purpose |
| --- | --- | --- |
| `GODSEYE_HOME` | `~/gods-eye-view` | Checkout that contains `server/mcp/stdio.js` |
| `GODSEYE_API_BASE` | `http://localhost:4173` | Where Hermes reaches the app |
| `GODSEYE_PUBLIC_URL` | the API base | URL a browser can open, used for share links and **Open full app** |
| `GODSEYE_NODE` | `~/.local/node24/bin/node`, else `node` | Node binary |
| `GODSEYE_PERF` | `embed` | `default`, `0`, or `off` leaves the viewer's normal profile |
| `GODSEYE_TURN_STATE` | on | `0` or `off` drops the per-turn map line. Watch alerts still use the hook |
| `GODSEYE_WATCH_LOOP` | on | `0` disables the watch thread |
| `GODSEYE_HUD_BRIDGE` | on | `0` disables the loopback HUD bridge |
| `GODSEYE_BRIDGE_PORT` | `18791` | HUD bridge port |
| `GODSEYE_LIVE_PORT` | `18792` | Live relay port |
| `GODSEYE_NOMINATIM_URL` | `https://nominatim.openstreetmap.org` | Geocoder for search and reverse |
| `GODSEYE_OVERPASS_URL` or `OVERPASS_UPSTREAMS` | public mirrors | Extra Overpass endpoints, tried first |
| `GODSEYE_VALHALLA_URL` | `https://valhalla1.openstreetmap.de` | Isochrones |
| `GODSEYE_OSRM_URL` | `https://routing.openstreetmap.de` | Routes, and the isochrone fallback |
| `GODSEYE_WAYBACK_CONFIG` | Esri's Wayback config JSON | Historical imagery release list |
| `GODSEYE_HTTP_USER_AGENT` | a `hermes-godseye/0.2` UA with this repo's URL | User-Agent for Nominatim, Overpass, and research |
| `GODSEYE_BIND` | `127.0.0.1` | Listen address for `install-service.sh` only |
| `GODSEYE_NODE_DIR` | `~/.local/node24` | Node directory for `install-service.sh` only |

If the app is reached by a hostname, add that name to God's Eye View's `GEV_ALLOWED_HOSTS`. That setting belongs to the app.

For watch alerts that should start a turn immediately, set Hermes `plugins.entries.godseye.allow_gateway_injection` to true. Queued delivery works without it.

### Doctor

`/gev doctor`, or `godseye_doctor`, is read-only. It checks Node, the checkout, that the app answers, patches 0001 and 0002, the live relay, connected panes and last-poll age, client-key presence (in `config.json` or, on an older build, the bundle), server-key presence, ion token scopes, panel freshness (`PANEL_STALE` / `KEY_MISSING`), the active map stack, layer feed states from `feedProvenance`, and the annotation count. Every failed check includes a fix. It installs nothing.

## Try these

- "What am I looking at?"
- "Fly to Tokyo and switch to night vision." Night vision is the `surveillance` visual style. The style list also includes `normal`, `retro`, `thermal`, `anime`, and `noir`.
- "Find ramen in Montreal and pin them."
- "How far is it to walk between these two pins, and what can I reach in 10 minutes?"
- "Show this block in 2014."
- "Brief me on the Strait of Hormuz and show it."
- "Any earthquakes above magnitude 5 in the last day?" This one works with the pane closed.
- "Watch fires in this view and tell me when a new one shows up."
- "Save this investigation and write a report."
- "Export this investigation as GeoJSON and KML." `format=pack` also writes a zip.
- "Find John Smith's house." This is refused.

Framing presets on `godseye_live_fly_to_location`: `street` is about 150 m at pitch −15°, `rooftop` is 450 m at −30°, `district` is 2,500 m at −45°. The minimum range is 100 m.

## Tools

`plugin.yaml` registers **98** tools, all in the `godseye` toolset. Every description includes the public-data policy. The groups below are the practical map. The full list is at the end of this section.

| Group | When to use it | Examples |
| --- | --- | --- |
| **Public feeds** (29) | A fact, a count, or a list. The pane can be closed. | `godseye_aircraft_in_area`, `godseye_get_earthquakes`, `godseye_situation_brief`, `godseye_military_awareness` |
| **Move the globe** | Fly, zoom, style, layers, map stack, follow a public vehicle or satellite. Needs the pane. | `godseye_live_fly_to_location`, `godseye_live_set_visual_style`, `godseye_live_set_layer_visibility`, `godseye_live_track_entity` |
| **Read the view** | "This", "here", distance, reach, before/after, counts of what is loaded. | `godseye_live_context`, `godseye_measure`, `godseye_isochrone`, `godseye_live_compare`, `godseye_live_analyst_query` |
| **Places and pins** | Search, drawers, clusters, filters, research. | `godseye_find_places`, `godseye_live_pin_drawer`, `godseye_live_filter_pins`, `godseye_research_pins` |
| **Keep it** | Home, share, tours, named investigations, reports, GeoJSON and KML. | `godseye_home`, `godseye_share_link`, `godseye_investigation_save`, `godseye_investigation_export` |
| **Watch** | New earthquakes, fires, flights, ships, or places in an area. | `godseye_watch_create`, `godseye_watch_list`, `godseye_watch_pause`, `godseye_watch_delete` |
| **Setup** | Health, panes, rebuild, HUD. | `godseye_doctor`, `godseye_live_panes`, `godseye_rebuild_panel`, `godseye_hud_summary` |

`godseye_find_places` needs no Overpass server of your own. Category and cuisine search tries your `GODSEYE_OVERPASS_URL` or `OVERPASS_UPSTREAMS`, then `overpass.kumi.systems`, `overpass.private.coffee`, and `overpass-api.de`. Each request gets 20 seconds, with 45 seconds in total. An endpoint that fails twice is skipped for 5 minutes. Names and addresses go to Nominatim, at most one request per 1.2 seconds, cached for an hour. If every Overpass mirror is down, the search falls back to a Nominatim keyword search in the same box. Radius is at most 15 km. Results include `countScope` and `© OpenStreetMap contributors (ODbL)`. `pin=true` puts up to 24 of them on the globe. `godseye_poi_details` returns a website, a menu URL when the site links one, and price text copied from the page. When nothing can be read, the price list is empty.

Large tools take `detail=concise` (the default) or `detail=full`. `detail` is removed before the arguments are sent upstream, because the MCP server and the pane runner reject unknown fields.

<details>
<summary><strong>Full list of 98 tools</strong></summary>

### Public feeds

These call the God's Eye View MCP server. They do not need the pane.

- `godseye_get_earthquakes` — USGS earthquakes of magnitude 2.5 or more, last 24 hours.
- `godseye_get_active_fires` — NASA FIRMS detections in an area, last 24 hours.
- `godseye_get_recent_launches` — Orbital launches from Launch Library 2, last 30 days.
- `godseye_aircraft_in_area` — Aircraft currently reported in an area.
- `godseye_find_aircraft` — Find a current aircraft by callsign, ICAO address, or registration.
- `godseye_get_aircraft_track` — Recent positions of one aircraft, thinned to at most 200 points.
- `godseye_get_aircraft_info` — Type, registration, and route from adsbdb.
- `godseye_vessels_in_area` — Ships currently reported by AIS in an area.
- `godseye_find_vessel` — Find a current ship by MMSI, IMO, or name.
- `godseye_get_vessel_track` — Recent positions of one ship, thinned to at most 200 points.
- `godseye_next_satellite_pass` — Next rise of a satellite over an observer.
- `godseye_satellites_overhead` — Satellites in a group above an observer now.
- `godseye_find_cctv_cameras` — Public traffic and city cameras in an area.
- `godseye_get_cctv_snapshot` — An image from one of those cameras. A Street View fallback is historical.
- `godseye_find_alpr_cameras` — ALPR cameras mapped in OpenStreetMap, United States or Canada, at most 3° per side.
- `godseye_get_traffic_flow` — TomTom road speeds in a city-sized area.
- `godseye_get_weather_map` — Latest NOAA radar, infrared, or lightning image.
- `godseye_get_recent_imagery` — Latest clear Landsat/Sentinel-2 (30 m), or VIIRS when that is missing.
- `godseye_find_submarine_cables` — TeleGeography cables and landing points.
- `godseye_find_infrastructure` — OSM datacenters or dams in an area.
- `godseye_get_bhote_koshi_flood` — The 26 August 2026 Bhote Koshi flood, as God's Eye View maps it.
- `godseye_get_cyclones` — Active tropical cyclones from the National Hurricane Center and the Central Pacific Hurricane Center.
- `godseye_get_fire_perimeters` — US WFIGS wildfire perimeters.
- `godseye_get_terrain_height` — Ground elevation at up to 20 points.
- `godseye_find_military_installations` — OSM military sites, at most 10° per side.
- `godseye_get_map_features` — Named OSM features at a location.
- `godseye_situation_brief` — Weather, quakes, fires, aircraft, ships, and cyclones for an area, each summarized.
- `godseye_military_awareness` — Military aircraft, other aircraft, ships, and mapped military sites around a location.
- `godseye_show_in_gods_eye_view` — Open a view in the pane, and return a link to the app.

### Live globe

These need the pane. They run upstream voice actions unless noted as added here.

- `godseye_live_fly_to_location` — Fly to a place or a coordinate. Optional `framing`: street, rooftop, district.
- `godseye_live_move_camera` — Orbit, pan, tilt, or rotate.
- `godseye_live_adjust_camera_zoom` — Move closer or farther without changing the target.
- `godseye_live_zoom_to_globe` — Pull out to a full-Earth view.
- `godseye_live_set_visual_style` — Visual filter, including surveillance (night vision) and thermal.
- `godseye_live_set_layer_visibility` — Enable or disable one data layer.
- `godseye_live_show_data_layers_menu` — Open the layers menu.
- `godseye_live_set_map_stack` — Switch the basemap stack.
- `godseye_live_set_post_processing` — Bloom and sharpen.
- `godseye_live_set_hud` — HUD visibility and layout.
- `godseye_live_set_cyber_sonar` — Sonar controls for the Cyber HUD layout.
- `godseye_live_set_detection` — Detection overlay.
- `godseye_live_set_panel_open` — Open or close an app panel.
- `godseye_live_set_context_mode` — Contacts or Space Missions, when asked.
- `godseye_live_control_cockpit` — Enter or leave the aircraft cockpit view.
- `godseye_live_control_scene` — Play or stop a named cinematic scene.
- `godseye_live_control_cctv` — Select and step through CCTV cameras.
- `godseye_live_control_radio` — Internet radio, without moving the map.
- `godseye_live_track_entity` — Follow an aircraft, ship, or satellite by its public id.
- `godseye_live_stop_tracking` — Stop following.
- `godseye_live_select_nearest_aircraft` — Fly somewhere, load flights, and select the nearest airborne aircraft.
- `godseye_live_frame_overhead` — Pull back so nearby aircraft, ships, or satellites are in frame.
- `godseye_live_get_current_view_state` — Camera, style, layers, and related view state. Prefer the turn line unless you need `detail=full`.
- `godseye_live_get_entity_context` — Basemap, 3D-tile target, and the selected entity.
- `godseye_live_analyst_query` — Counts and filters over records loaded by enabled layers.
- `godseye_live_resolve_area` — Name a place and get an `areaId`. `draw=true` outlines it.
- `godseye_live_find_imagery` — Recent Sentinel-2, Landsat, or VIIRS, pinned in the app's imagery panel.
- `godseye_live_osm_query` — One kind of OSM place in an area. Needs an Overpass server in the app, or the plugin's fallback.
- `godseye_live_next_iss_pass` — Next ISS pass over the camera or a lat/lon.
- `godseye_live_next_satellite_pass` — Next pass of one satellite in the loaded catalog.
- `godseye_live_annotate_map` — Pins, routes, labels, and card callouts. Pass `intent` so the drawer matches the request.
- `godseye_live_clear_annotations` — Erase annotations. Only when the user asks.
- `godseye_live_fly_route` — Fly along a route already drawn with `annotate_map`.

### Added by this plugin

- `godseye_hud_summary` — One HUD caption from `ctx.llm`, using the app's HUD prompt.
- `godseye_brief_and_show` — Brief a place and show it: fly, layers, top pins, caption. The brief still returns if the pane is closed.
- `godseye_live_context` — Camera, focus, drawings, reverse geocode, nearby places.
- `godseye_what_is_here` — The same lookup for a coordinate, with no pane.
- `godseye_live_draw` — Start draw mode, or add, list, and clear shapes.
- `godseye_measure` — Distance, area, or an OSRM route.
- `godseye_isochrone` — Valhalla reach rings, or an approximate OSRM polygon.
- `godseye_live_compare` — Wayback / OSM / current swipe.
- `godseye_find_places` — OSM and Nominatim place search. `pin=true` drops pins.
- `godseye_poi_details` — Website, menu link, and verbatim prices for one venue.
- `godseye_live_pin_drawer` — Open, close, or read the pin drawer.
- `godseye_live_filter_pins` — Category, rating, open-now, and cluster controls.
- `godseye_live_list_annotations` — Annotations stored for this chat's scene.
- `godseye_live_delete_annotation` — Delete one stored annotation by id.
- `godseye_live_restore_scene` — Redraw the stored scene. Reopening the pane does this on its own.
- `godseye_research_pins` — Wikipedia, Wikidata, Commons, OSM tags, and website meta. `mode` is `light` or `full`.
- `godseye_live_run_tour` — Background tour. Returns a `jobId` immediately.
- `godseye_live_tour_status` — Progress of a tour job.
- `godseye_live_stop_tour` — Cancel a tour.
- `godseye_home` — `go`, `set`, or `get` the saved home view.
- `godseye_share_link` — A share URL for the current view, on `GODSEYE_PUBLIC_URL`.
- `godseye_investigation_save` — Save the scene under `plugin-data/godseye/investigations/`.
- `godseye_investigation_list` — List saved investigations.
- `godseye_investigation_load` — Load one and restore it on the pane.
- `godseye_investigation_delete` — Delete one. Requires `confirm=true`.
- `godseye_investigation_report` — HTML and Markdown under `plugin-data/godseye/reports/`.
- `godseye_investigation_export` — GeoJSON and KML under `plugin-data/godseye/exports/`. `format=both` is the default. `format=pack` adds a zip of the GeoJSON, the KML, the Markdown report, and any saved screenshots. Photorealistic tile imagery is not embedded.
- `godseye_watch_create` — Watch an area. Feeds: `earthquakes`, `fires`, `flights`, `ships`, `pois`.
- `godseye_watch_list` — List watches. `check=<id>` runs one now.
- `godseye_watch_pause` — Pause or resume a watch.
- `godseye_watch_delete` — Delete a watch. Requires `confirm=true`.
- `godseye_live_panes` — List panes, prune stale ones, or set a label.
- `godseye_live_reload_pane` — Re-fetch the panel and rebuild the frame.
- `godseye_rebuild_panel` — `npm run build:panel` with the env file exported, then reload open panes.
- `godseye_preview_swap` — Open one desktop preview URL and close the previous URL this plugin opened.
- `godseye_doctor` — The health check above.

</details>

## `/gev` and the analyst skill

`/gev` is a Hermes slash command (`ctx.register_command`). It refuses the same person-tracking phrases the tools refuse.

```text
/gev fly <place>
/gev layers [on|off <names>]
/gev status
/gev brief <place>
/gev home [set]
/gev share
/gev doctor
/gev research [light|full]
/gev save <name>
/gev list
/gev load <name>
/gev delete <name> --confirm
/gev report [name]
/gev export [name] [geojson|kml|both|pack]
```

The analyst skill is `skills/godseye-analyst/SKILL.md`, registered as `godseye-analyst` (Hermes shows it as `godseye:godseye-analyst`). It tells the model when to use a data tool, when to use a live tool, to call `godseye_live_context` for "this" and "here", to trust the map line, to pass `detail=full` only when it needs the payload, and to follow `fix` on errors. Long playbooks live in the skill. Tool schemas stay short.

## Performance

Three mechanisms keep a session from thrashing the GPU or the context window. None of the figures below are timings measured in this repository. The cache sizes are the fork's documented `perf=embed` settings ([`docs/EMBEDDING.md`](https://github.com/mojomast/gods-eye-view/blob/main/docs/EMBEDDING.md)).

**`perf=embed`.** `GODSEYE_PERF` defaults to `embed`. The pane adds `?perf=embed` and sends `perf:'embed'` in `gev:config`. On the fork, that profile sets:

| Knob | Embed setting |
| --- | --- |
| 3D tile `cacheBytes` | 384 MB |
| `maximumCacheOverflowBytes` | 128 MB |
| `targetFrameRate` | 30 |
| `msaaSamples` | 2 |
| Screen-space error | 16, raised to 24 while the camera moves |
| Dynamic and foveated screen-space error | on |
| `preserveDrawingBuffer` | off; a capture renders, then reads the canvas in the same turn |

`GODSEYE_PERF=default` (also `0` or `off`) leaves the viewer's normal profile. A stock upstream build that does not read `perf` ignores the flag. The fork's doc also says a hidden document, or `gev:visibility` with `visible: false`, does not render a frame for a capture.

**Concise results.** Feed lists, briefs, images, live context, measure, isochrones, compare, research, investigation load/report, pin filters, and watch create/list default to `detail=concise`: ids and counts, images replaced with a byte count. A short distance, an area, or an open/closed flag stays intact. Route lines, polygons, long catalogs, and screenshots wait for `detail=full`.

**A pane that stays up.** The pane is created with `lifecycleKeepAlive`, and the panel document is cached, so a layout resize does not remount the iframe. On dispose the frame calls `WEBGL_lose_context` and destroys the viewer, which returns the context to the browser. A lost context or a failed Cesium startup shows **Click to reload the globe**. Hiding the pane sends `gev:visibility` so a current fork build pauses rendering, HUD timers, and feed polls until the pane is visible again.

## Privacy and responsible use

Eye Of Hermes is for public, aggregate data: aircraft, vessels, satellites, hazards, weather, infrastructure, and mapped places. `gev/policy.py` refuses requests that ask to search for a named person, recognise a face, or find, locate, track, or follow a person. The refusal is `code=refused`, and the fix tells the model to stop and offer a public-asset view instead. Tracking a flight by callsign or ICAO address, a ship by MMSI, or a satellite by NORAD id is supported. Tracking a person is not a feature.

**Photorealistic 3D tiles are for looking.** They are Google Photorealistic 3D Tiles, reached through Cesium ion. Google's [Map Tiles API policies](https://developers.google.com/maps/documentation/tile/policies) allow them for visualization. They are not a source to index, to run detection on, or to mine into a dataset. This plugin's measurements, briefs, and watches use the public feeds and OSM. They do not interpret the tile imagery. `godseye_investigation_export` writes the investigation's own pins, drawings, and routes. It does not embed those tiles. A pack zip may include screenshots the user already saved, and it skips photorealistic tile images.

**Nominatim and Overpass are shared public services.** Nominatim's [usage policy](https://operations.osmfoundation.org/policies/nominatim/) asks for a real User-Agent, about one request per second, and no bulk scraping. This plugin waits 1.2 seconds between Nominatim calls, sends a User-Agent that names `hermes-godseye` and this repository, and caches responses for one hour. Point `GODSEYE_NOMINATIM_URL` at your own instance if you outgrow the public one. Overpass queries are bounded (15 km radius for place search) and back off when a mirror fails. Please stay inside those mirrors' fair-use rules. OSM data in place results is credited `© OpenStreetMap contributors (ODbL)`.

**Cameras.** `godseye_find_cctv_cameras`, `godseye_get_cctv_snapshot`, `godseye_find_alpr_cameras`, and the live CCTV controls are in the catalog because they are in God's Eye View. They are public-camera and mapped-camera tools. They are not a way to identify a person, and the policy refusal still applies to their arguments.

**What the plugin contacts**, when the matching tool or watch runs:

| From the plugin process | Service |
| --- | --- |
| Place search and reverse geocoding | Nominatim; Overpass mirrors (and any endpoint you configured) |
| Routes and isochrones | OSRM at `routing.openstreetmap.de`; Valhalla at `valhalla1.openstreetmap.de` |
| Historical imagery metadata | Esri Wayback config on AWS. The pane loads the tiles from `wayback.maptiles.arcgis.com` |
| Earthquake watches | USGS FDSN |
| Fire watches | NASA FIRMS (the key is sent to FIRMS; it is not logged) |
| Flight watches | OpenSky Network anonymous API, which rate-limits |
| Ship watches | The local God's Eye View app (`vessels_in_area`), which holds the AIS stream |
| Place watches | The same Overpass / Nominatim path as place search |
| Pin research | `en.wikipedia.org`, `www.wikidata.org`, `commons.wikimedia.org`, and the venue's own site for a meta description |
| Everything else in a feed tool | The God's Eye View app, which then calls the providers in its own DATA_SOURCES |

The pane's browser also requests whatever the viewer needs for the active stack: Cesium ion, Google 3D tiles, Esri imagery, OpenStreetMap tiles, Mapillary, and the app's live layers. The relay and the HUD bridge listen on `127.0.0.1` only.

**Files.** The plugin writes under `~/.hermes/plugin-data/godseye/` (or `$HERMES_HOME/plugin-data/godseye/`): the HUD bridge env (mode 0600, URL plus a random token), home view, scenes, watches, investigations, reports, GeoJSON/KML exports, and the research cache. It also rewrites `dist/panel/config.json` in the God's Eye View checkout. It does not read Hermes credential stores. This repository has no telemetry client and no self-updater.

Drawer content is escaped before it is shown. Only http(s) images and links are kept. The host opens those links. The frame does not.

## Troubleshooting

Start with `/gev doctor`. The fix under each failed check is the one to follow.

| What you see | What it means |
| --- | --- |
| `no_pane` | Open the Eye Of Hermes pane in Hermes Desktop or the Webapp and keep it visible, then retry the same call. Data tools still work. |
| `pane_busy` | The pane is still on an earlier command. Wait a few seconds and retry that same call once. |
| `relay_unavailable` | Restart the Hermes dashboard or gateway with the plugin enabled, so the relay starts. |
| `timeout` | Retry once. If it happens again, run `godseye_live_panes` and `godseye_doctor` before changing arguments. |
| `upstream_down` | The app is not answering `GODSEYE_API_BASE`. Start it with `npm run dev`. |
| `KEY_MISSING` / `PANEL_STALE` | Add the named key to the env file, mode 0600. On a current build, open the pane so `config.json` is rewritten. On an older build, run `godseye_rebuild_panel`. |
| `ION_SCOPES` | Issue a Cesium ion token limited to `assets:read` and `assets:list`. |
| Blank globe, or CesiumWidget / WebGL init failed | The browser is out of WebGL contexts. Close the tab (a reload may not free them) and open the pane again. A context lost later shows **Click to reload the globe**. |
| `routing_unavailable` | OSRM or Valhalla did not answer. Distance still works with `godseye_measure` `op=distance`. Set `GODSEYE_OSRM_URL` or `GODSEYE_VALHALLA_URL` to point at your own servers. |
| `OVERPASS_DOWN` | Retry `godseye_find_places` once. Do not fill in places from memory. |
| **Open full app** opens localhost | Set `GODSEYE_PUBLIC_URL` to a URL the browser can reach, and add that host to the app's `GEV_ALLOWED_HOSTS`. |
| Full UI toggle missing | Doctor will say patch 0002 is missing. Apply it and rebuild the panel. |
| Plugin changes never appear | Rescan plugins in Hermes, toggle the pane, and hard-refresh the Webapp. |

Live control needs the pane open and loaded. Session binding needs a Hermes Desktop build that exposes session ids on `host.state`. Without them, commands go to the most recently active pane.

The tool snapshots (`gev/tools_snapshot.json`, `gev/live_tools_snapshot.json`) have to be refreshed when God's Eye View changes its catalogs. `scripts/snapshot_tools.sh` does that.

## Roadmap

GeoJSON and KML export, and the recorded feed fixtures, are in this version. See `godseye_investigation_export` above, and Development below.

Still outside this tree:

- **Fewer tools.** [Pull request #3](https://github.com/mojomast/hermes-godseye/pull/3) is phase A: `godseye_view` and `godseye_query` call the tools that already exist, and the current names stay registered.
- **WebGL recovery.** A lost context shows **Click to reload the globe**. The follow-on destroys the viewer, creates it again, and restores the saved scene. It does not have a pull request yet. `lifecycleKeepAlive` and the visibility pause stay.

## Credits

- **[God's Eye View](https://github.com/bilawalsidhu/gods-eye-view)** by Bilawal Sidhu and contributors, MIT. This plugin reuses its MCP server, its globe panel, its voice actions, and its HUD prompt. The upstream license is included as [`LICENSE.upstream-gods-eye-view`](LICENSE.upstream-gods-eye-view). Eye Of Hermes is an independent integration. It is not affiliated with or endorsed by the upstream project.
- The embed protocol (`gev:config`, `gev:visibility`, `perf=embed`) comes from the [mojomast fork](https://github.com/mojomast/gods-eye-view).
- Pane imagery in the screenshots is credited on screen to Cesium ion and Esri, via God's Eye View.

## License

**hermes-godseye** © 2026 mojomast, [MIT](LICENSE).

## Development

```sh
python3 -m pytest -q tests
```

The usual run blocks public network. Contract tests for measure, isochrones, place search, area watches, and investigation export replay hand-written cassettes under `tests/fixtures/cassettes`: an OSRM route, a Valhalla isochrone, Overpass places, Nominatim search and reverse, USGS earthquakes, a FIRMS CSV, and OpenSky states. Those files contain no keys. `GODSEYE_E2E=1` is the only opt-in for a live call against `npm run dev`, and it is not for CI.
