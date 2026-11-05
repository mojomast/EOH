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
`godseye_find_places {query: "ramen restaurants in Montreal", pin: true}` pins them with drawer content
(address, hours, website, links). To add your own notes to a pin, include `title`, `description_md`
(markdown), `images:[{url, alt}]` and `links:[{url, label}]` (http/https only) in its `annotate_map`
spec. Show one with `godseye_live_pin_drawer {action: "open", label: "Sumo Ramen", flyTo: true}`;
close with `{action: "close"}`. The user can also click any pin. Never put private URLs in a drawer.

**"Save this as my home view" / "go home"** → `godseye_home {action: "set"}` / `{action: "go"}`.

**"Send me a link to this."** → `godseye_share_link`.

Slash command for quick control: `/gev fly <place>`, `/gev layers [on|off names]`,
`/gev status`, `/gev brief <place>`, `/gev home [set]`, `/gev share`, `/gev doctor`.
