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

## godseye_feeds

Public facts. Most ops work with the pane closed. One op per call. `detail: "full"` for rows or an image.

Hazards: `earthquakes`, `fires` (needs `area`), `launches`, `flood`, `cyclones`, `fire_perimeters` (needs `area`).
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
| `annotate` | `annotations` required. Pass `intent` (the user's words) whenever you create pins. Fill `description_md`, `images`, and `links` (http/https only) when you know them. |
| `clear` | Erase marks. Only when the user asks. |
| `draw` | `action: "start"` and `kind` (`box`, `polygon`, `line`) hands the pane to the user. `action: "add"` draws your own shape from `points`. |
| `list` | Stored marks for this chat. |
| `delete` | One stored mark. `id` required. |
| `filter` | Category, rating, open-now chips. `action`: `set`, `state`, `expand`. |
| `drawer` | Open or close one pin drawer (`op=drawer`). `action`: `open`, `close`, `state`. Pick the pin by `id`, `label`, or lat/lon. |
| `research` | Fill drawers (`op=research`) from Wikipedia, Wikidata, Commons, OSM tags, and a site's meta description. `mode`: `light` or `full`. Never invent facts. This plugin cannot call Hermes `web_search`. Pass verified snippets as `agent_notes`. |

## godseye_nav

| op | Use |
| --- | --- |
| `measure` | `op=measure`. Argument `measure` is `distance`, `area`, or `route` (not a second `op`). Pass `points`, `pins`, or `use_drawn: "latest"`. `mode` for a route is `walk`, `bike`, or `drive`. |
| `isochrone` | `op=isochrone`. Minutes of reach. `minutes: [15]`, `mode: "walk"`. If `approximate` is true, say so. If `routing_unavailable`, say so. Do not draw a straight-line circle. |
| `fly_route` | Dolly along a route already drawn. This is not a camera fly. |
| `compare` | `op=compare`. Imagery swipe. `left` / `right`: `current`, `osm`, or `wayback:YYYY[-MM[-DD]]`. `action: "list_dates"` lists Wayback releases. `action: "stop"` ends it. Map stacks cannot be split. |

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
| `tour` | `stops` required. Returns a `jobId` immediately. |
| `tour_status` | `jobId` required. |
| `tour_stop` | Cancel a tour. |
| `playback` | Cinematic scene. `action`: `list`, `play`, `stop`, `next`, `status`. Not an investigation. |

## godseye_watch

Only when the user asks. Feeds: `earthquakes`, `fires`, `flights`, `ships`, `pois`.

| op | Use |
| --- | --- |
| `create` | `area` and `feeds`. Area is a bbox, a polygon, a center plus radius, or `{view: true}`. |
| `list` | `check` set to an id runs that watch now. There is no separate check op. |
| `pause` | `id` required. |
| `delete` | `id` required. `confirm: true` only after the user agrees. |

When the turn has "[EOH watch alert]" lines, tell the user. The new items are already pinned. A missing key is `KEY_MISSING`. Say so. Do not invent the feed.

## godseye_capture

| op | Use |
| --- | --- |
| `hud` | `visible`: `on`, `off`, `auto`. `layout`: `tactical`, `minimal`, `cyber`. |
| `summary` | One caption from the Hermes model. `context` is locale, place, altitude, and layer counts. |

## godseye_admin

| op | Use |
| --- | --- |
| `doctor` | Read-only health check. It installs nothing. |
| `rebuild` | Rebuild `dist/panel` and reload open panes. A current build reads `config.json` and does not need this for a key change. |
| `reload` | Re-fetch the panel into the open pane. |
| `preview` | One desktop preview tab. `url` to open. `action`: `swap` or `close`. |

## Rules

1. **Public assets and events only.** Aircraft, vessels, satellites, hazards, weather, infrastructure. Never search for a named person, recognise faces, or track or locate an individual, even if they are "on" an aircraft or ship. Decline and offer the public-data view.
2. **Orient from the map line.** Each turn may include `EOH pane=...` (connected or busy, camera, layers, pins, drawer). Trust it. Call `godseye_query` `op=context` when the user is asking what they see, not to rediscover the camera. Use `godseye_view` `op=state` with `detail: "full"` only when the line is missing and you need the full object.
3. **Check results are capped.** Read `total` and `truncated` before saying "there is nothing".
4. **Sections can be unavailable** (fires need a FIRMS key). Say so instead of guessing.
5. **Failures include `code`, `message`, and `fix`.** Do what `fix` says (open the pane, wait and retry once, pass `confirm=true` only after the user agrees). `no_pane` means the Eye Of Hermes pane is closed: feeds still work. `pane_busy` means wait and retry the same call once.

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

**"How many flights are in view?"**
`godseye_query` `{op:"analyst", layers:["flights"], scope:{kind:"view"}}`.

**"Save this as my home view" / "go home" / "send me a link."**
`godseye_view` `{op:"home", action:"set"}`, `{op:"home", action:"go"}`, `{op:"share"}`.

**"Save this investigation as Montreal Ramen" / "load it" / "export it."**
`godseye_scene` `{op:"save", name:"Montreal Ramen"}`, then `{op:"load", name:"Montreal Ramen"}`.
`{op:"report", name:"Montreal Ramen", capture:true}` writes HTML and Markdown.
`{op:"export", name:"Montreal Ramen", format:"pack"}` writes GeoJSON and KML, plus a zip. Omit `name` to export the current scene. Google photoreal tile imagery is not included.
Delete only with `{op:"delete", name:"Montreal Ramen", confirm:true}`.

Slash command: `/gev fly`, `layers`, `status`, `brief`, `home`, `share`, `doctor`, `research`, `save`, `list`, `load`, `delete`, `report`, `export`.

## Internal mapping

Retired Hermes names are not tools. Do not call them. `gev/router.py` `dispatch_legacy` runs a saved call that still uses one. Scenes, tours, `/gev`, the relay, and the pane keep bare action names such as `fly_to_location`. The map is `docs/COMPOSABLE-TOOLS.md`.
