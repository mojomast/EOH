---
name: godseye-analyst
description: Eye Of Hermes (EOH) analyst playbook (built on God's Eye View). Prefer godseye_view and godseye_query. Legacy godseye_* names still work. Public-data-only policy, pane handling, and example sequences.
---

# Eye Of Hermes (EOH) analyst playbook

Eye Of Hermes (EOH) is a Hermes front end for God's Eye View, a live 3D globe of **public** aircraft (ADS-B), ships (AIS), satellites, earthquakes, fires, weather, cyclones and mapped infrastructure.

Prefer two composable tools. Pass `op` every time. Legacy names in the appendix call the same handlers.

## godseye_view

Camera, home, share link, and the pane list. Needs the pane, except `home` action `get`.

| op | Use |
| --- | --- |
| `fly` | Place (`query`) or coordinates (`latitude`/`longitude`, or `lat`/`lon`). `framing`: `street` (150 m, pitch -15), `rooftop` (450 m, -30), `district` (2500 m, -45). `rangeM` overrides framing (minimum 100). `style`: `surveillance` is NVG / night vision (`nvg` is accepted). `thermal` is FLIR. Style is applied after a successful fly. |
| `move` | `motion`: `orbit`, `pan`, `tilt`, `rotate`, `rotate_globe`, `stop`. `direction` for pan/tilt/rotate. `speed`: `slow`, `normal`, `fast`. `mode`: `once` or `continuous`. |
| `zoom` | Relative only. `direction`: `in` or `out`. `amount`: `little`, `medium`, `lot`. |
| `globe` | Whole Earth. Do not use `zoom` for this. |
| `home` | `action`: `go` (default), `set`, `get`. |
| `share` | Link to the current view. |
| `panes` | List panes. `label` names this chat's pane. `prune` drops stale rows. |

`fanout: true` only when the user wants every open pane changed (`fly`, `move`, `zoom`, `globe`).

## godseye_query

Places, the open view, and briefs. Data ops work without the pane. `here: true` means the open pane's camera.

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

Large answers default to `detail: "concise"`. Pass `detail: "full"` for rows, geometry, or an image.

## Rules

1. **Public assets and events only.** Aircraft, vessels, satellites, hazards, weather, infrastructure. Never search for a named person, recognise faces, or track or locate an individual, even if they are "on" an aircraft or ship. Decline and offer the public-data view.
2. **Orient from the map line.** Each turn may include `EOH pane=...` (connected or busy, camera, layers, pins, drawer). Trust it. Call `godseye_query` `op=context` when the user is asking what they see, not to rediscover the camera. Use `godseye_live_get_current_view_state` with `detail: "full"` only when the line is missing and you need the full object.
3. **Check results are capped.** Read `total` and `truncated` before saying "there is nothing".
4. **Sections can be unavailable** (fires need a FIRMS key). Say so instead of guessing.
5. **Failures include `code`, `message`, and `fix`.** Do what `fix` says (open the pane, wait and retry once, pass `confirm=true` only after the user agrees). `no_pane` means the Eye Of Hermes pane is closed: data tools still work. `pane_busy` means wait and retry the same call once.

## The pane

- Live ops act on the pane next to this chat, else the most recently active pane.
- If a live tool returns `no_pane`, tell the user to open the Eye Of Hermes (EOH) pane in Hermes Desktop or the Webapp and keep it visible.
- A hidden pane is paused. Client keys live in `dist/panel/config.json`. Do not print key values.
- `godseye_view` `op=panes` lists panes and last-poll age.

## Filters, clusters, and watches

These names are not composable yet. Keep using them.

- Pins from `find_places` carry `tags`. `godseye_live_filter_pins` narrows them, for example `{categories:["ramen"], open_now:true}`. `open: null` means the hours are unknown.
- Create a watch (`godseye_watch_create`) only when the user asks. Report `KEY_MISSING` plainly. `godseye_watch_delete` needs `confirm: true`. `godseye_watch_list` with `check` runs one watch now.
- When the turn has "[EOH watch alert]" lines, tell the user. The new items are already pinned.

## Measure, reach, compare, draw

- Distance or area: `godseye_measure` with `points`, `pins`, or `use_drawn: "latest"`. Travel time is `op: "route"` plus `mode` (`walk`, `bike`, `drive`). That inner `op` belongs to measure until `godseye_nav` exists.
- "What can I reach in 15 minutes walking": `godseye_isochrone` with `minutes: [15]` and `mode: "walk"`. If `approximate` is true, say so. If `routing_unavailable`, say so. Do not draw a straight-line circle.
- Before/after imagery: `godseye_live_compare` with `left: "wayback:2015"` and `right: "current"`. `action: "stop"` ends it.
- The user draws with the toolbar, or you start draw mode with `godseye_live_draw` `action: "start"`.

## Example sequences

**"Fly to Tokyo NVG."**
`godseye_view` `{op:"fly", query:"Tokyo", style:"surveillance"}`. Add a layer only if they asked for one, with `godseye_live_set_layer_visibility`.

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

## Pins always carry drawer content

Pass `intent` (or `context`) on every pin-creating call: `find_places` with `pin: true`, `brief_and_show`, `godseye_live_annotate_map`, and `godseye_live_run_tour`. Set it to the user's request. Fill `description_md`, `images`, and `links` (http/https only) when you know them. Open one drawer with `godseye_live_pin_drawer` `{action:"open", label:"Sumo Ramen"}`. Light enrichment runs automatically. `godseye_research_pins` `{mode:"full"}` is the deeper pass. This plugin cannot call Hermes `web_search`; pass verified snippets as `agent_notes`. Never invent facts.

**"Save this investigation" / "load it" / "export a report."**
`godseye_investigation_save`, `godseye_investigation_load`, `godseye_investigation_report`. Delete only with `godseye_investigation_delete` `{confirm:true}`.

Slash command: `/gev fly`, `layers`, `status`, `brief`, `home`, `share`, `doctor`, `research`, `save`, `list`, `load`, `delete`, `report`.

## Legacy names

Phase A names below still work. Prefer the composable op. The full map, including tools that are not composable yet (layers, feeds, annotate, nav, track, scene, watch, capture, state, admin), is `docs/COMPOSABLE-TOOLS.md`.

| Legacy name | Call instead |
| --- | --- |
| `godseye_live_fly_to_location` | `godseye_view` `op=fly` |
| `godseye_live_move_camera` | `godseye_view` `op=move` |
| `godseye_live_adjust_camera_zoom` | `godseye_view` `op=zoom` |
| `godseye_live_zoom_to_globe` | `godseye_view` `op=globe` |
| `godseye_home` | `godseye_view` `op=home` |
| `godseye_share_link` | `godseye_view` `op=share` |
| `godseye_live_panes` | `godseye_view` `op=panes` |
| `godseye_find_places` | `godseye_query` `op=find_places` |
| `godseye_live_osm_query` | `godseye_query` `op=osm` |
| `godseye_live_context` | `godseye_query` `op=context` |
| `godseye_what_is_here` | `godseye_query` `op=what_is_here` |
| `godseye_situation_brief` | `godseye_query` `op=brief` |
| `godseye_brief_and_show` | `godseye_query` `op=brief_and_show` |
| `godseye_live_analyst_query` | `godseye_query` `op=analyst` |
| `godseye_military_awareness` | `godseye_query` `op=military` |
| `godseye_poi_details` | `godseye_query` `op=poi` |
| `godseye_live_resolve_area` | `godseye_query` `op=resolve_area` |
| `godseye_live_get_entity_context` | `godseye_query` `op=entity` |
