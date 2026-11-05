# Changelog

## 0.2.0 (unreleased)

### Added
- `godseye_brief_and_show`: runs situation_brief or military_awareness, then flies the live globe, enables the brief's layers, pins the top N hits and sets a caption line. Returns the brief plus what was shown, and still returns the brief when no pane is open. If upstream geocoding is unavailable, it retries with coordinates.
- Multi-pane live relay. Panes register an id and their chat session ids, and commands go to the session's pane, else the most recently active ready pane. Every live tool takes an opt-in `fanout`. New `godseye_live_panes` tool and `/live/panes` route. `pane_busy` is now distinct from `no_pane`.
- `/gev` slash command (`fly`, `layers`, `status`, `brief`, `home [set]`, `share`, `doctor`) via `ctx.register_command`.
- Bundled skill `godseye:godseye-analyst` (analyst playbook) via `ctx.register_skill`.
- `godseye_doctor` health check with fix text (Node 24, checkout, :4173, patches #1571/#1572, relay, panes and last-poll age). It is read-only.
- Saved home view (`godseye_home`, `/gev home`) in `ctx.state` with a `plugin-data/godseye/home.json` mirror. The pane cold-starts at home (`/home` route).
- `godseye_share_link`: upstream share-codec `/share/<code>` link on `GODSEYE_PUBLIC_URL`, with a camera-hash fallback.
- In-frame caption line (`hermes_caption`) over the globe.

### Changed
- The live relay no longer hands a command to whichever pane polls first.
- Version 0.2.0. Every new Hermes API is optional: older builds skip the command, skill or state and still register the tools.

## 0.1.0
- First release: 29 data tools, 33 live-globe tools, Hermes HUD summaries, and the Desktop/Webapp pane.
