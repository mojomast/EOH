---
name: godseye-analyst
description: Eye Of Hermes (EOH) analyst playbook (built on God's Eye View). When to use the godseye_* data tools vs the godseye_live_* globe tools, how to handle the pane, the public-data-only policy, and example sequences.
---

# Eye Of Hermes (EOH) analyst playbook

Eye Of Hermes (EOH) is a Hermes front end for God's Eye View, a live 3D globe of **public** aircraft (ADS-B), ships (AIS), satellites,
earthquakes, fires, weather, cyclones and mapped infrastructure. In Hermes it comes as two kinds
of tools:

| Need | Use |
| --- | --- |
| A fact, count or list ("how many ships near Rotterdam?") | `godseye_*` data tools (work without the pane) |
| "Show me", "what am I looking at", change the view | `godseye_live_*` tools (need the pane open) |
| An overview of a place **and** the globe pointed at it | `godseye_brief_and_show` |
| Something is off (no data, no pane, blank globe) | `godseye_doctor` |
| A link someone else can open | `godseye_share_link` |
| The user's usual starting view | `godseye_home` (`go` / `set` / `get`) |
| Research a pin's drawer (summary, photos, sources) | `godseye_research_pins` |
| Save / load a whole investigation | `godseye_investigation_save` / `_load` / `_list` / `_delete` |
| Export an HTML+Markdown report | `godseye_investigation_report` |

## Rules

1. **Public assets and events only.** Aircraft, vessels, satellites, hazards, weather,
   infrastructure. Never search for a named person, recognise faces, or track or locate an
   individual, even if they are "on" an aircraft or ship. Decline and offer the public-data view.
2. **Data first, then show.** Answer from a data tool (or `godseye_situation_brief` /
   `godseye_military_awareness`), then optionally show it live. Do not describe the screen
   without reading it: call `godseye_live_get_current_view_state` first.
3. **Check results are capped.** Read `total` and `truncated` before saying "there is nothing".
4. **Sections can be unavailable** (e.g. fires need a FIRMS key). Say so instead of guessing.

## The pane

- Live tools act on the EOH pane next to this chat (else the most recently active
  pane). If a live tool returns `no_pane`, tell the user to open the Eye Of Hermes (EOH) pane in
  Hermes Desktop or the Webapp and keep it visible; data tools still work.
- `pane_busy` means a pane is open but still running an earlier command: wait a few seconds and
  retry once. `godseye_live_panes` lists panes with their last-poll age.
- Only set `fanout: true` on a live tool when the user explicitly wants every open pane changed.

## Example sequences

**"What's going on around Tokyo? Show me."**
`godseye_brief_and_show {place: "Tokyo"}` then summarise `brief`, mention what was shown.

**"Any military activity near San Diego?"**
`godseye_brief_and_show {place: "San Diego", question: "military activity"}` (picks
`military_awareness`, pins the nearest contacts in red).

**"What am I looking at?"**
`godseye_live_get_current_view_state`, then `godseye_live_analyst_query` for counts if needed.

**"Night-vision view of the Strait of Hormuz with ships."**
`godseye_live_fly_to_location {query: "Strait of Hormuz", waitForArrival: true}` →
`godseye_live_set_layer_visibility {layerId: "ais-live-vessels", enabled: true}` →
`godseye_live_set_visual_style {style: "surveillance"}`.

**"List ramen places in Montreal" / "tell me about that pin."**
`godseye_find_places {query: "ramen restaurants in Montreal", pin: true, intent: "ramen near me"}`
pins them with drawer content shaped by the request (cuisine, hours, menu/website). Always pass
`intent` and fill `description_md` / `images` / `links` when you know more. Show one with
`godseye_live_pin_drawer {action: "open", label: "Sumo Ramen", flyTo: true}`; close with
`{action: "close"}`. Never put private URLs in a drawer.

**"Save this as my home view" / "go home"** → `godseye_home {action: "set"}` / `{action: "go"}`.

**"Send me a link to this."** → `godseye_share_link`.


## Pins always carry drawer content

**Every pin you create must have non-empty drawer content tailored to the user's request.**

1. Pass `intent` (or `context`) on every pin-creating call — `godseye_live_annotate_map`,
   `godseye_find_places {pin:true}`, `godseye_brief_and_show`, `godseye_live_run_tour` — set to the
   user's request in their words (e.g. `"ramen near me"`, `"FIRMS fires last 24h"`).
2. Always fill `description_md` yourself when you know the facts. Prefer the facts the request
   cares about: cuisine / hours / price / menu link for food; detection time / confidence / source
   for fires; magnitude / depth for quakes; callsign / altitude for aircraft; etc. Add `images[]`
   and `links[]` (http/https only) when you have them.
3. The plugin light-enriches every pin automatically (cached, fast) from tags already on the pin.
   Call `godseye_research_pins {mode:"full"}` when the user asks for a deeper pass (Wikipedia /
   Wikidata / Commons / website meta). `mode:"light"` is the default enrichment path.
4. If a pin somehow lands empty, the plugin auto-enriches it in the background and pushes the
   update to the pane drawer — still pass `intent` so that pass is relevant.
5. This plugin cannot call Hermes `web_search`. When free sources are thin, run your own web
   search and pass verified snippets as `agent_notes` on `godseye_research_pins`, or rewrite the
   pin via `annotate_map`. Never invent facts; mark missing data as unavailable.

**"Research these pins" / "fill in the drawers."**
`godseye_research_pins {mode:"full"}` (or `{ids:["a1","a2"]}`). Then open one with
`godseye_live_pin_drawer`.

**"Save this investigation as Montreal Ramen" / "load it" / "export a report."**
`godseye_investigation_save {name:"Montreal Ramen", notes:"..."}` →
`godseye_investigation_load {name:"Montreal Ramen"}` →
`godseye_investigation_report {name:"Montreal Ramen", capture:true}`.
Delete only with `godseye_investigation_delete {name:"...", confirm:true}`.

Slash command for quick control: `/gev fly <place>`, `/gev layers [on|off names]`,
`/gev status`, `/gev brief <place>`, `/gev home [set]`, `/gev share`, `/gev doctor`,
`/gev research [light|full]`, `/gev save|list|load <name>`, `/gev delete <name> --confirm`,
`/gev report [name]`.
