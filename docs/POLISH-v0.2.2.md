# EOH polish v0.2.2 — bug list for Orbit bees

Branch: `feat/polish-v0.2.2` off `main` @ `8cb2cb8` (mojomast/EOH, plugin id `godseye`).
Worktree (box): the EOH polish worktree. PC project key: `eoh` with `sync_branch: feat/polish-v0.2.2`.
Keep **exactly 11** composable tools. Prefer new ops / clearer errors over new tools. Report o200k schema delta via `python3 evals/measure_schemas.py`. Tests: `python3 -m pytest -q tests` (+ own file first). Never commit inside the sandbox; leave an uncommitted diff. Never merge to main, never tag, never force-push, never touch moltussy-1.

Hard rules for every bee: change ONLY `assigned_files`; do NOT git commit/branch/stash; write the test first and confirm it fails on the base; no secrets/tailnet/private paths in code or notes.

---

## P01 — Black globe after imagery / zoom / pin drawer (P0)

**Repro:** Imagery A/B on, zoom out, or open a pin drawer (e.g. Louvre). Globe goes black; persists after reload.
**Likely cause:** Wayback tile template in `gev/measure.py`:
`…/MapServer/tile/{release}/{z}/{y}/{x}` — release `43291` returns HTTP 301 → `7110`. Releases must be resolved from `waybackconfig.json` (already fetched by `wayback_releases()`). Tools that claim imagery success must fail when nothing rendered.
**Files:** `gev/measure.py`, `tests/test_wayback_releases.py` (new)
**Acceptance:**
- `pick_release` / tile URL builders never emit a release id that is not in the current waybackconfig mapping (fixture with 43291→7110 style redirect entry).
- When imagery apply cannot confirm tiles, return `ok:false` with a clear code (reuse `wayback_unavailable` or add `imagery_failed`) and a `fix`, never a bare success.
- Own tests + full pytest green; 11 tools; schema delta reported; only assigned_files.

## P02 — Geocode Chicago + weather preset never completes (P0)

**Repro:** Fly/geocode `Chicago` → "Couldn't find Chicago". Weather preset hangs / never completes under `pane_busy` / `no_pane`.
**Likely cause:** Nominatim query shaping in `gev/poi.py` `geocode`; weather preset in `gev/ext_weather.py` turns layers on and waits on a busy/hidden pane without a short clear failure.
**Files:** `gev/poi.py`, `gev/ext_weather.py`, `tests/test_ext_weather.py`, `tests/test_geocode_chicago.py` (new)
**Acceptance:**
- `geocode("Chicago")` (and common US city forms) returns a hit with fixture/fake nominatim; clear `no_match` / `geocoder_unavailable` when empty.
- Weather preset: on `pane_busy`/`no_pane`/`timeout`, return structured failure with `fix` (retry once guidance); do not spin past ~one retry.
- Do not re-enable layers the user turned off unless the preset was explicitly requested (coordinate with P10 — here only avoid unconditional enable when pane missing).
- Own + full pytest; 11 tools; schema delta; scope.

## P03 — Transit `follow_vehicle` pretends to work (P0)

**Repro:** `godseye_track op=follow_vehicle` returns ok, but live `track_entity` only supports flights/military/AIS/satellites. Missing `vehicle_id` → `bad_args`.
**Files:** `gev/ext_transit.py`, `tests/test_ext_transit.py`
**Acceptance:**
- Implement a real transit follow path if the live pane exposes a supported action; otherwise return `ok:false` with a clear `unsupported` (or similar) error and `fix`, never a false ok.
- Missing `vehicle_id` stays `bad_args` with `fix`.
- Tests cover both paths; full pytest; 11 tools; scope.

## P04 — Fire BAD_AREA radius + statewide `fires_failed` (P1)

**Repro:** Center areas without `radius_km` → `BAD_AREA: center areas need radius_km`. First statewide call → `fires_failed`.
**Files:** `gev/ext_fire.py`, `tests/test_ext_fire.py`
**Acceptance:**
- Center/{lat,lon} areas default a sensible `radius_km` (document in blurb; e.g. 25–50 km) instead of hard-failing.
- FIRMS/statewide fetch: one retry/backoff on transient failure; final error still structured.
- Own + full pytest; 11 tools; scope.

## P05 — Pin research wrong Louvre article + unstable pin IDs (P1)

**Repro:** Research matched Pavillon de l'Horloge instead of Louvre; pin IDs rewritten.
**Files:** `gev/research.py`, `tests/test_research_grounding.py` (new)
**Acceptance:**
- Grounding prefers exact/label+geosearch distance; reject far-off homonyms when a closer title exists (fixture: Louvre vs Pavillon de l'Horloge).
- Research updates never change pin `id`; only drawer fields.
- Own + full pytest; 11 tools; scope.

## P06 — Overlays `relay_unavailable` + open sidebar op (P0)

**Repro:** `op=overlays` fails with `relay_unavailable` when the relay singleton lives in another process. No op opens the Overlays sidebar visually.
**Fix:** Apply `docs/hermes-live-overlays-relay.patch` (HTTP fallback via `live.call('/overlays', bridge.ensure_token(), body)`). Add an action/arg to open the sidebar (e.g. `action: "open"` / `expand` that drives `panel_overlays.js`). Skill prose for the HTTP note is **P09**.
**Files:** `gev/overlays.py`, `gev/panel_overlays.js`, `tests/test_overlays.py`
**Acceptance:**
- When `live.current()` is None, HTTP fallback is used; only true outage returns `relay_unavailable` with `fix`.
- New/extended action opens the Overlays sidebar in the pane chrome (unit-test the message/ hook; no browser).
- Own + full pytest; 11 tools; schema delta small; scope.

## P07 — WebGL context exhaustion / fragile recovery (P1)

**Repro:** "too many WebGL contexts"; time slider failed in Cape Town after recovery.
**Files:** `gev/gl_recover.py`, `gev/gl_recover.js`, `tests/test_gl_recover.py`, `gev/gl_recover.test.mjs`
**Acceptance:**
- Recovery destroys/recreates a single viewer and restores scene; does not leak extra contexts (assert dispose called).
- Timeline/wayback resume after recovery is documented in return payload or re-applied once.
- JS + py tests green; full pytest; scope.

## P08 — Commands to hidden/stale panes + 150s timeout (P1)

**Repro:** Commands route to hidden/stale panes; "pane did not answer" waits ~150s.
**Files:** `gev/live.py`, `tests/test_live_pane_routing.py` (new)
**Acceptance:**
- Prefer the most recently focused **visible** pane for session routing.
- Cut command wait from ~150s to ~20–30s; timeout error stays clear with `fix`.
- Align related stale constants so inflight prune stays slightly above the new budget.
- Own + full pytest; scope. **Do not** change desktop/plugin.js timeouts in this task (report if a paired desktop change is needed).

## P09 — Skill: layers, no skill_manage, overlays HTTP note (P1)

**Repro:** Hermes re-enables layers the user turned off; calls `skill_manage` on godseye-analyst / edits plugin code.
**Files:** `skills/godseye-analyst/SKILL.md` only
**Acceptance:**
- Explicit rule: never re-enable user-disabled layers unless the user asked; presets must say so.
- Explicit rule: do not call `skill_manage` on godseye-analyst; do not edit plugin source from chat.
- Include the overlays HTTP-fallback / sidebar-button note from `docs/hermes-live-overlays-relay.patch` (skill hunk only).
- No Python schema change expected; pytest still green; scope = skill file only.

## P10 — Code: never re-enable user-disabled layers (P1)

**Repro:** Composer / scene restore / weather / transit turn layers on blindly.
**Files:** `gev/composer.py`, `gev/scene.py`, `tests/test_layer_user_pref.py` (new)
**Deps:** none (coordinate mentally with P02/P03 — do not edit ext_* here)
**Acceptance:**
- Scene restore and composer respect stored disabled layers; only enable when the user/op explicitly requests that layer.
- Tests with a scene that has `flights` disabled stay disabled across restore/compose.
- Own + full pytest; scope.

## P11 — Cesium: entity outlines on terrain + heightReference without height (P2)

**Repro:** Cesium warnings in console.
**Files:** `gev/panel_pins.js` (and only that file unless a tiny shared helper already exists in-file)
**Acceptance:**
- Entities that use `heightReference` set a height (or drop heightReference when clamp-to-ground without height).
- Outline/terrain warning path fixed or avoided for pins.
- Prefer a small node test or document manual check in notes if no existing harness; full pytest still green.

## P12 — Feed-down status + flights → adsb.lol fallback (P2)

**Repro:** AIS timeout unclear; flights fallback to adsb.lol not reported.
**Files:** `gev/providers.py`, `gev/watch.py`, `tests/test_feed_status.py` (new)
**Acceptance:**
- Doctor/status/watch paths surface feed-down (e.g. AIS timeout) with status + `fix`.
- When flights use adsb.lol fallback, response includes an explicit `fallback` / note field.
- Own + full pytest; scope.

## P13 — Markdown tables render as run-on in Hermes chat (P3)

**Repro:** Tables appear as one line in Hermes Webapp chat.
**Files:** `docs/KNOWN_ISSUES.md` (new or update)
**Acceptance:**
- Document whether EOH emits GFM tables vs upstream Webapp limitation; if upstream, no code change — just docs. If EOH can emit a safer format for multi-column summaries, note the recommendation only (no router change in this task).

## P15 — `godseye_capture` rejects `detail` (P2)

**Repro:** Schema rejects `detail` even though docs/skill mention detail elsewhere.
**Files:** `gev/router.py` (capture schema section only), `tests/test_capture_detail.py` (new)
**Acceptance:**
- Either accept `detail: concise|full` on capture ops that benefit, or remove `detail` from any capture docs touched in-scope. Prefer accept with default concise; keep schema token growth small.
- Own + full pytest; 11 tools; measure schema delta; scope.

---

## Dispatch waves

- **Wave A (dispatch now, ≤6):** P01, P03, P04, P05, P06, P07
- **Held for deps / second wave:** P02 (poi+weather), P08 (live.py), P09 (skill), P10 (composer/scene), P11, P12, P13, P15

Coordinator: the EOH bees orch dir (`python3 orch.py status|dispatch`). Model: `deepseek-v4.1-flash` only. Cap concurrent polish bees at 6. No bulk-retry onto other paid models.
