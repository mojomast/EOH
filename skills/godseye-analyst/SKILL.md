---
name: godseye-analyst
description: Eye Of Hermes (EOH) analyst playbook (built on God's Eye View). Eleven composable tools, each with op. Public-data-only policy, pane handling, and example sequences.
---

# Eye Of Hermes (EOH) analyst playbook

Eye Of Hermes (EOH) is a Hermes front end for God's Eye View, a live 3D globe of **public** aircraft (ADS-B), ships (AIS), satellites, earthquakes, fires, weather, cyclones and mapped infrastructure.

Eleven tools. Pass `op` every time. Long guidance is here. Schemas stay short.

## godseye_view

Camera, a composed view, the full camera read, home, share link, and the pane list.

| op | Use |
| --- | --- |
| `fly` | Place (`query`) or coordinates (`latitude`/`longitude`, or `lat`/`lon`). `framing`: `street` (150 m, pitch -15), `rooftop` (450 m, -30), `district` (2500 m, -45). `rangeM` overrides framing (minimum 100). `style`: `surveillance` is NVG / night vision (`nvg` is accepted). `thermal` is FLIR. Style is applied after a successful fly. |
| `move` | `motion`: `orbit`, `pan`, `tilt`, `rotate`, `rotate_globe`, `stop`. |
| `zoom` | Relative only. `direction`: `in` or `out`. `amount`: `little`, `medium`, `lot`. |
| `globe` | Whole Earth. Do not use `zoom` for this. |
| `home` | `action`: `go` (default), `set`, `get`. |
| `share` | Link to the current view. |
| `panes` | List panes. `label` names this chat's pane. `prune` drops stale rows. |
| `show` | One composed view: `view`, `area`, `camera`, `layers`, `style`, `map`, `annotations`, `follow`. |
| `state` | Full camera read. Prefer the EOH map line. `detail: "full"` only when that line is missing. |

`fanout: true` only when the user wants every open pane changed.

## godseye_query

Places, the open view, elevation, named features, and briefs. Data ops work without the pane. `here: true` means the open pane's camera.

| op | Use |
| --- | --- |
| `context` | "What am I looking at", this, here, that building. Camera, focus, reverse geocode, nearby places. Call this before guessing a coordinate. |
| `what_is_here` | Reverse geocode a `lat`/`lon`. No pane unless `here: true`. |
| `find_places` | "ramen near me", "ramen in Montreal". `here: true` or a near-me phrase searches around the camera (default 1500 m). `pin: true` and `intent` set to the user's words. |
| `osm` | In-pane OSM places. `what` is required (`hospitals`). Omit `areaId` to use the view. |
| `resolve_area` | Named place to an `areaId`. `draw: true` outlines it. Pass `candidateId` when asked which place. |
| `analyst` | Counts and lists for enabled layers. Ships are `ais-live-vessels`. Fires are `local-firms`. `scope.kind: "view"` is "in view". |
| `brief` | Situation brief. `place`, `area`, or `here: true` for this area. |
| `brief_and_show` | Brief a place and show it (fly, layers, pins, caption). Still returns the brief when the pane is closed. |
| `military` | Military contacts around a place. |
| `poi` | One venue by `name` and optional `city`. Never invent prices. |
| `entity` | Selected or in-view record. `scope`: `auto`, `selected`, `in_view`, `pointer`. |
| `terrain` | Ground elevation. `points` is a list of `{lat, lon}` (at most 20). |
| `features` | Named OSM features. `location` plus `kind`: `administrative_areas`, `named_places`, or `monuments`. |

Large answers default to `detail: "concise"`. Pass `detail: "full"` for rows, geometry, or an image.

## godseye_layers

Pane chrome and the look of the globe. Needs the pane.

| op | Required | Notes |
| --- | --- | --- |
| `visibility` | `layerId`, `enabled` | One layer on or off. |
| `menu` | | Open the layers menu. |
| `panel` | `panelId`, `open` | Side panel. |
| `context_mode` | `mode` | `off`, `contacts`, `flights`, `space-missions`, `missions`. |
| `style` | `style` | `normal`, `retro`, `surveillance`, `thermal`, `anime`, `noir`. `nvg` and night vision mean surveillance. `flir` means thermal. |
| `sonar` | | Cyber HUD sonar. `enabled` turns it on. |
| `detection` | | Overlay. `mode`: `sparse`, `balanced`, `dense`. |
| `stack` | `stack` | `photoreal`, `bing-aerial`, `bing-labels`, `esri-imagery`, `osm`. |
| `post` | | `bloom` and `sharpen`. |

## Public SDR receivers

`sdr-receivers` is a directory layer, not a tuner. Requires mojomast/gods-eye-view main at or after the merge of pull request #5. `godseye_layers` `op=visibility` with `layerId=sdr-receivers`, `enabled=true` shows public receivers on the globe. Default pins are the ~1,325 KiwiSDR/OpenWebRX receivers that can stream both audio and a waterfall; turn off `MAP: AUDIO + WATERFALL ONLY` in the PUBLIC SDR card to show all ~1,876 receivers, including WebSDR, UberSDR, SpyServer, and HTTPS-only sites.

A pin click is the user's explicit play gesture: it selects the receiver in the PUBLIC SDR card, starts audio, and opens the floating spectrum/waterfall. Never start a stream without a click or an explicit request. One stream at a time. Stop on STOP, Radio-panel collapse, pane hide (`gev:visibility`), or selecting another receiver.

Only `http://` KiwiSDR and OpenWebRX play natively in the pane. HTTPS receivers, WebSDR, UberSDR, and SpyServer do not; the card links out with `OPEN ↗`. Receiver pages are linked, never framed. Do not weaken the pane sandbox (`allow-same-origin` is forbidden) and do not claim audio is playing unless the card says `Playing`.

Deep link: `?sdr=<id>&f=<MHz>&m=<mode>&wf=1&play=1`, for example `?sdr=kiwi%3A91.234.129.51%3A8888&f=7.14&m=lsb&wf=1&play=1`. The waterfall is a floating, movable, resizable window; the globe stays usable behind it. It exposes a dB scale, peak/SNR, S-meter, noise floor, FFT size, users, coverage, tuning step, mode, zoom/pan/full-band, pause, and a PNG snapshot.

If the pane is down, say so: feeds still work, but the map, pins, and audio do not.

## godseye_feeds

Public facts. Most ops work with the pane closed. One op per call. `detail: "full"` for rows or an image. A bbox is `{south, west, north, east}` or `{place, radius_km}`. A four-number list is accepted only when one order is a real box.

Hazards: `earthquakes`, `fires` (needs `area`), `launches`, `flood`, `cyclones`, `fire_perimeters` (needs `area`), `fire_check` (place, bbox, or the open pane).
Aircraft: `aircraft_in_area` (`area`), `find_aircraft`, `aircraft_track` (`icao24`), `aircraft_info`.
Ships: `vessels_in_area` (`area`), `find_vessel`, `vessel_track` (`mmsi`).
Satellites: `next_pass` and `overhead` take `location` and do not need the pane. `iss_pass` and `satellite_pass` (`target`) use the pane catalog.
Weather and imagery: `weather` (`map`: `radar`, `satellite`, `global_satellite`, `lightning`), `imagery` (`area`), `imagery_live`.
Infrastructure: `cables`, `infrastructure` (`kind`: `datacenters` or `dams`, plus `area`), `military_sites` (`area`). The military brief is `godseye_query` `op=military`.
Cameras and radio: `find_cctv` (`area`), `cctv_snapshot` (`camera_id`), `alpr` (`area`), `traffic` (`area`), `cctv_control` (`action`), `radio` (`action`).

CCTV and ALPR are public or mapped cameras, not a way to identify a person. ALPR search is OpenStreetMap cameras in the United States or Canada, at most 3° per side. A Street View fallback on a snapshot is historical. Do not read a plate, a face, or a person out of a camera image. The refusal still applies to every argument, including text nested in `area`.

## godseye_annotate

Pins and drawings on the open pane.

| op | Use |
| --- | --- |
| `annotate` | `annotations` required, or `source` (a `.jsonl`, `.csv`, or `.json` path), or `records` (`[{lat, lon, label, description_md, images, links, tags, category, id}]`). Pass `intent` whenever you create pins. `replace: true` updates a pin with the same `id` or within 25 m. Exact duplicates are skipped. |
| `clear` | Erase marks. Only when the user asks. |
| `draw` | `action: "start"` and `kind` (`box`, `polygon`, `line`) hands the pane to the user. `action: "add"` draws your own shape from `points`. |
| `list` | Stored marks for this chat. `detail: "full"` includes title, description, images, links, and tags. Clips are included either way when the pin has them. |
| `delete` | One mark (`id`) or several (`ids`). |
| `update` | Patch `label`, `description_md`, `images`, `links`, or `tags` on one pin. No redraw. |
| `filter` | Category, rating, and open-now. The Overlays sidebar holds the same filters. `action`: `set`, `state`, `expand`. `op=filter` still sets them. |
| `overlays` | Sidebar tree of pins, routes and trails, areas, watches, and saved scenes. `action`: `tree` (default), `open` (`expand` is accepted), `show`, `hide`, `delete`, `update`, `filters`, `export`, `import`. `id` is an entry (`ann:`, `shape:`, `watch:`, `scene:`) or a group (`kind:pins`, `kind:routes/batch:…`). Delete of a group, a watch, or a scene needs `confirm: true`. `update` changes `label`, `title`, or `description_md`. `export` writes `eoh.overlays.v1` JSON under `plugin-data/godseye/overlays/`. `import` reads `source` or `document`. `replace` (default true) replaces this scene's marks. Watches and scenes are upserted and are not deleted unless `purge` and `confirm` are both true. The sidebar is the pane chrome's "Overlays" button; `action: "open"` (or `show` with no id) opens it, so `tree` only reports and points at the button. `op=overlays` runs in-process (`gev/overlays.py` `run()`), unlike the other live ops that POST to the relay: if it returns `relay_unavailable` while `godseye_layers` or `godseye_annotate` still work, the relay is bound in another process (the desktop app) and this process's module cache is stale. `run()` uses an HTTP fallback to `POST /overlays`, which takes effect for a fresh process; from a live process run `python3 -c "from gev import overlays; overlays.run({'op':'overlays','action':'tree'}, SID)"` until Hermes restarts. |
| `drawer` | Open or close one pin drawer (`op=drawer`). `action`: `open`, `close`, `state`. Pick the pin by `id`, `label`, or lat/lon. |
| `clip` | Attach a YouTube or X status URL to a stored pin. `url` required. `title` optional. YouTube accepts `start_sec` (seconds, or a clock such as `1m30s`, or `t=` / `start=` on the URL). The same URL updates in place. At most 4 clips. List returns them. The drawer shows a youtube-nocookie or X embed plus the https link. Other hosts are refused. |
| `research` | Fill drawers (`op=research`) from Wikipedia, Wikidata, Commons, OSM tags, and a site's meta description. `mode`: `light` or `full`. Never invent facts. This plugin cannot call Hermes `web_search`. Pass verified snippets as `agent_notes`. |

## godseye_nav

| op | Use |
| --- | --- |
| `measure` | `op=measure`. Argument `measure` is `distance`, `area`, or `route` (not a second `op`). Pass `points`, `pins`, or `use_drawn: "latest"`. `mode` for a route is `walk`, `bike`, or `drive`. |
| `isochrone` | `op=isochrone`. Minutes of reach. `minutes: [15]`, `mode: "walk"`. If `approximate` is true, say so. If `routing_unavailable`, say so. Do not draw a straight-line circle. |
| `fly_route` | Dolly along a route already drawn. This is not a camera fly. Pass `id` for an exact saved route, or `points` / `waypoints` / `stops` for coordinates. A label matches exactly before a fuzzy match, and a fuzzy match must hit one route. A dense waypoint list is thinned and sent in chunks of about 300 points. `lighten` defaults to true: Street Level turns off and the globe uses a coarse tile setting until the dolly ends, then both are restored. Pass `lighten: false` to keep the current Street Level and quality. |
| `street_walk` | Walk a saved route in Street View. Argument `walk` is `start`, `pause`, `resume`, `step`, `stop`, or `state` (not a second `op`). Pass `label` of a drawn route, or `points` / `waypoints` / `stops`. Default spacing is about 75 m (`stepM`, 20–250). `dwellS` is seconds at each panorama (default 4). `provider` is `google` or `mapillary`. `state` reports progress. A reload interrupts the walk; `walk=resume` continues it. |
| `street_level` | Open one panorama. Argument `street` is `at`, `next`, `prev`, or `route` (not a second `op`). `at` takes `lat`/`lon`. `next` and `prev` step from the last panorama. `route` uses the same route resolution as `fly_route`. |
| `itinerary` | Build a `drive` or `walk` route through `pins` (ids or `{lat, lon}`), draw it as one mark, and optionally set `dolly` or `streetStops`. |
| `compare` | `op=compare`. Imagery swipe. `left` / `right`: `current`, `osm`, or `wayback:YYYY[-MM[-DD]]`. `action: "list_dates"` lists Wayback releases. `action: "stop"` ends it. Map stacks cannot be split. |
| `timeline` | Time slider. Argument `timeline` is `set`, `play`, `step`, `stop`, `state`, or `list_dates` (not a second `op`). `mode`: `wayback` (Esri Wayback releases over the current view, `transition` `swipe` or `fade`) or `events` (USGS quakes and NASA FIRMS fires, plus stored watch alerts, as pins that appear when the playhead reaches them). `from` / `to` / `date` are `YYYY-MM-DD`. Events default to the last 7 days. `area` is `{south, west, north, east}`, `[south, west, north, east]`, or `{place, radius_km}`. `feeds`: `earthquakes` and/or `fires`. `by` on `step` is `1` or `-1`. `state` reads the date and what is visible. Wayback tiles are for viewing only. Do not sample photorealistic 3D tiles. The slider is saved with the scene and with investigations. |

## godseye_track

Follow a public vehicle or satellite. Not a person.

| op | Use |
| --- | --- |
| `track` | Follow one entity. `query` is a callsign, ICAO address, MMSI, or NORAD id. |
| `stop` | Stop following. |
| `select_nearest` | `layerId` required. Fly, enable that layer, select the nearest. |
| `frame` | `target`: `flights`, `military`, `satellites`, or `vessels`. This is not the street/rooftop/district framing on fly. |
| `cockpit` | `action`: `enter`, `exit`, `previous`, `next`, `status`. |

## godseye_scene

Named investigations, the live scene, tours, and export. Files land under `plugin-data/godseye/`.

| op | Use |
| --- | --- |
| `save` | `name` required. |
| `list` | Saved investigations. |
| `load` | `name` required. Restores the pane. |
| `delete` | `name` and `confirm: true`. Only after the user agrees. |
| `report` | HTML and Markdown. `capture: true` asks the pane for a map image. |
| `export` | `op=export`. GeoJSON (RFC 7946, longitude then latitude) and KML. `format`: `geojson`, `kml`, `both` (default), or `pack`. `pack` adds a zip of the GeoJSON, the KML, the Markdown report, and saved screenshots. Omit `name` to export the current scene. Do not embed photorealistic tile imagery. |
| `restore` | Re-apply this chat's scene. Reopening the pane does this on its own. |
| `tour` | `stops` required, or the same `source` / `records` row schema as annotate. Returns a `jobId` immediately. |
| `tour_status` | `jobId` required. |
| `tour_stop` | Cancel a tour. |
| `playback` | Built-in cinematic scene. `action`: `list`, `play`, `stop`, `next`, `status`. Not an investigation. This does not create a scene. |
| `director` | Build a Director Mode scene from `brief` (approach, overhead, detail) or from `scene` / `shots`, then apply it. `place`, or `lat` and `lon`, or the open pane. The same title keeps the same id and replaces that scene. Other Director scenes stay. A local `scene-assets` path needs `attribution` and `license`. YouTube and X come back as `third_party_media`; attach them with `godseye_annotate` `op=clip`. |
| `director_update` | `id` required. Replace that scene and apply it again. A new `brief` rebuilds the shots. |
| `director_list` | Scenes on the pane. Hermes scenes that were not applied are included with `applied: false`. |
| `director_play` | `id` required (scene id or title). Returns immediately. Stop with `director_stop`. |
| `director_stop` | Stop Director playback. |

If the pane has no `sceneDirector`, these ops return `director_unavailable` (or `director_import_unavailable` / `director_project_unreadable`) with `code`, `message`, and `fix`. Do not say the scene played.

## godseye_watch

Only when the user asks. Feeds: `earthquakes`, `fires`, `flights`, `ships`, `pois`.

| op | Use |
| --- | --- |
| `create` | `area` and `feeds`. Area is `{south, west, north, east}`, a bbox list, `{place, radius_km}`, a polygon, a center plus radius, or `{view: true}`. |
| `list` | `check` set to an id runs that watch now. There is no separate check op. |
| `pause` | `id` required. |
| `delete` | `id` required. `confirm: true` only after the user agrees. |

When the turn has "[EOH watch alert]" lines, tell the user. The new items are already pinned. A missing key is `KEY_MISSING`. Say so. Do not invent the feed.

## godseye_capture

| op | Use |
| --- | --- |
| `hud` | `visible`: `on`, `off`, `auto`. `layout`: `tactical`, `minimal`, `cyber`. |
| `summary` | One caption from the Hermes model. `context` is locale, place, altitude, and layer counts. |
| `pane` | PNG of the open pane, including the drawer, pins, and imagery. |

## godseye_admin

| op | Use |
| --- | --- |
| `doctor` | Health check. It installs nothing. `probe: true` or `detail: "full"` makes one cheap call per keyed provider and returns `ok`, `invalid`, `quota`, or `unreachable`. A TomTom row says to reload the pane so a new key is picked up. |
| `batch` | `calls` is `[{tool, args}, ...]`, at most 16. `stop_on_error` stops after the first failure. One result per call. |
| `status` | Control room: app, relay, panes, WebGL, and feeds. Read-only. Missing keys are names only. |
| `app_start` | Start the God's Eye View app. Linux only. User-triggered. Waits for HTTP 200. |
| `app_stop` | Stop that app. Refuses when the port belongs to another process. |
| `app_restart` | Restart that app. Same ownership rules. An open socket is not ready. |
| `rebuild` | Rebuild `dist/panel` and reload open panes. A current build reads `config.json` and does not need this for a key change. |
| `reload` | Re-fetch the panel into the open pane. The result includes `unsavedMarks`. Pass `confirm: true` only after the user agrees to drop marks that could not be snapshotted. |
| `preview` | One desktop preview tab. `url` to open. `action`: `swap` or `close`. |

## Rules

1. **Public assets and events only.** Aircraft, vessels, satellites, hazards, weather, infrastructure. Never search for a named person, recognise faces, or track or locate an individual, even if they are "on" an aircraft or ship. Decline and offer the public-data view.
2. **Orient from the map line.** Each turn may include `EOH pane=...` (connected or busy, camera, layers, pins, drawer). Trust it. Call `godseye_query` `op=context` when the user is asking what they see, not to rediscover the camera. Use `godseye_view` `op=state` with `detail: "full"` only when the line is missing and you need the full object.
3. **Check results are capped.** Read `total` and `truncated` before saying "there is nothing".
4. **Sections can be unavailable** (fires need a FIRMS key). Say so instead of guessing.
5. **Failures include `code`, `message`, and `fix`.** Do what `fix` says (open the pane, wait and retry once, pass `confirm=true` only after the user agrees). `no_pane` means the Eye Of Hermes pane is closed: feeds still work. `pane_lost` means a pane that was open stopped answering: ask the user to reopen it (the saved scene comes back) and offer to resume a walk or dolly. `pane_busy` means wait and retry the same call once.
6. **Never re-enable user-disabled layers unless the user asked.** Do not turn on a layer the user turned off unless the user asked for that layer (or asked for a named preset that includes it). A preset must say which layers it would enable and ask before changing user-disabled layers; it must not restore them silently. `op=visibility` changes only the layer named in the call.
7. **Do not manage this skill or edit the plugin from chat.** Never call `skill_manage` on `godseye-analyst`. Never edit plugin source (`gev/`, `desktop/`, `dashboard/`, or this skill) from chat. Changes go through a reviewed repository patch; this playbook only describes tool use.

## The pane

- Live ops act on the pane next to this chat, else the most recently active pane.
- If a live tool returns `no_pane`, tell the user to open the Eye Of Hermes (EOH) pane in Hermes Desktop or the Webapp and keep it visible.
- A hidden pane is paused. Client keys live in `dist/panel/config.json`. Do not print key values.
- `godseye_view` `op=panes` lists panes and last-poll age.

## Example sequences

**"Fly to Tokyo NVG."**
`godseye_view` `{op:"fly", query:"Tokyo", style:"surveillance"}`. Add a layer only if they asked, with `godseye_layers` `{op:"visibility", layerId:"flights", enabled:true}`.

**"What am I looking at?"**
Read the `EOH pane=` line. `godseye_query` `{op:"context"}`. Answer from `focus` and `here`. Name the source (OSM / Nominatim). If focus is only the view centre and they mean one object, ask them to click it or draw a box.

**"Ramen near me."**
`godseye_query` `{op:"find_places", query:"ramen near me", here:true, pin:true, intent:"ramen near me"}`.

**"Brief me on this area."**
`godseye_query` `{op:"brief", here:true}`.

**"What's going on around Tokyo? Show me."**
`godseye_query` `{op:"brief_and_show", place:"Tokyo"}`, then summarise `brief` and what was shown.

**"Any military activity near San Diego?"**
`godseye_query` `{op:"brief_and_show", place:"San Diego", question:"military activity"}` or `{op:"military", location:{place:"San Diego"}}`.

**"Show public SDR receivers."**
`godseye_layers` `{op:"visibility", layerId:"sdr-receivers", enabled:true}`, then `godseye_query` to the place. A pin click is the play gesture; if the user names a receiver without clicking, open the PUBLIC SDR card or the deep link rather than starting audio silently.

**"How many flights are in view?"**
`godseye_query` `{op:"analyst", layers:["flights"], scope:{kind:"view"}}`.

**"Show me how this area changed from 2016 to 2024."**
`godseye_nav` `{op:"timeline", timeline:"set", mode:"wayback", from:"2016-01-01", to:"2024-12-31", transition:"swipe"}`, then `{op:"timeline", timeline:"play"}` or `{op:"timeline", timeline:"step", by:1}`. `{op:"timeline", timeline:"state"}` reads the release on screen. `{op:"timeline", timeline:"list_dates", from:"2016-01-01", to:"2024-12-31"}` lists the releases first. Imagery is for viewing only.

**"Make a Director scene of the fire over Austin."**
`godseye_scene` `{op:"director", brief:"night fire approach over the basin", place:"Austin"}`. Then `{op:"director_list"}`, `{op:"director_play", id:"hermes-night-fire-approach-over-the-basin"}`, and `{op:"director_stop"}`. A structured `scene` or `shots` array works too. If the result is `director_unavailable`, say the pane has no Director. Do not invent an endpoint.

**"Put this YouTube clip on the basin pin."**
Annotate the pin, then `godseye_annotate` `{op:"clip", id:"a1", url:"https://youtu.be/abcdefghijk", title:"Basin", start_sec:30}`. `{op:"list"}` returns `clips`. Open the drawer to see the embed and the link. An X status URL works the same way, without a start time.

**"Check this area for fire."**
`godseye_feeds` `{op:"fire_check", place:"Los Angeles"}`, or `/gev fire Los Angeles`, or the same call inside `godseye_admin` `op=batch`. The sequence is FIRMS hotspots, the clearest recent image, then imagery before/after. It does not create a watch or a pin. No hotspots is a successful empty check. Say `summary`.

**"Replay fires here over the last 7 days."**
`godseye_nav` `{op:"timeline", timeline:"set", mode:"events", feeds:["fires"]}`, then `{op:"timeline", timeline:"play"}`. Omit `from` and `to` for the last 7 days, and omit `area` to use the open view. Pins show when the playhead reaches them. Open one with `godseye_annotate` `{op:"drawer", id:"..."}`. NASA FIRMS covers the last 10 days; older fires come from stored watch alerts.

**"Save this as my home view" / "go home" / "send me a link."**
`godseye_view` `{op:"home", action:"set"}`, `{op:"home", action:"go"}`, `{op:"share"}`.

**"Save this investigation as Montreal Ramen" / "load it" / "export it."**
`godseye_scene` `{op:"save", name:"Montreal Ramen"}`, then `{op:"load", name:"Montreal Ramen"}`.
`{op:"report", name:"Montreal Ramen", capture:true}` writes HTML and Markdown.
`{op:"export", name:"Montreal Ramen", format:"pack"}` writes GeoJSON and KML, plus a zip. Omit `name` to export the current scene. Google photoreal tile imagery is not included.
Delete only with `{op:"delete", name:"Montreal Ramen", confirm:true}`.

Slash command: `/gev fly`, `layers`, `status`, `brief`, `fire`, `home`, `share`, `doctor`, `research`, `save`, `list`, `load`, `delete`, `report`, `export`. `/gev fire` with no place uses the open pane.

## Internal mapping

Retired Hermes names are not tools. Do not call them. `gev/router.py` `dispatch_legacy` runs a saved call that still uses one. Scenes, tours, `/gev`, the relay, and the pane keep bare action names such as `fly_to_location`. The map is `docs/COMPOSABLE-TOOLS.md`.
