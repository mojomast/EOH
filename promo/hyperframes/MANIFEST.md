# Original-media handoff — locked v6 edit

Reuse the exact GEV map-pane selects and Ash WAVs from the merged promo at
`edcb673`. These files are intentionally untracked. Do not record substitutes,
change source trims, accelerate clips, regenerate Ash or shift scene boundaries.

Each basename below requires `vo/<basename>.wav`. Scenes 01–10 also require
`footage/<basename>.mp4`. Scenes 11–13 are designed graphics and need no capture.
The original captures already contain the held pre-action frames and final holds.
Playback starts at source time 0, speed 1, at each scene's absolute start.

| Scene / basename | Film start | Duration | Original local map-action cue |
|---|---:|---:|---:|
| `01-cold-open` | 0.000 | 5.967 | — |
| `02-hook` | 5.967 | 7.100 | 2.600 |
| `03-hermes-globe` | 13.067 | 7.600 | 2.600 |
| `04-feeds` | 20.667 | 8.600 | 3.133 |
| `05-pin-research` | 29.267 | 9.267 | 2.767 |
| `06-director` | 38.534 | 8.267 | 3.267 |
| `07-fire-check` | 46.801 | 13.467 | 3.476 |
| `08-transit-follow` | 60.268 | 7.867 | 2.867 |
| `09-imagery-time` | 68.135 | 11.000 | 2.992 |
| `10-overlays` | 79.135 | 12.333 | 3.333 |
| `11-architecture` | 91.468 | 7.400 | — |
| `12-stats` | 98.868 | 5.700 | — |
| `13-outro` | 104.568 | 7.750 | — |

Scene 02 keeps the whole-earth take and its original **simulated** Hermes reply,
labelled accordingly in the terminal. Scene 07 keeps the official 2021 Caldor
perimeter followed by forest before/after. Scene 09 keeps Hollywood Park,
completed SoFi and the split comparison. Scene 10 keeps the Richmond/Kew route
and Overlays sidebar. All original prompt text is frozen in `edit-lock.json`.

`captions.json` is the unchanged Ash transcript and timing; its SHA-256 is checked
on every build. Original WAVs play at gain 1 without retiming or source processing.
Their previous production target was approximately −16 LUFS. Measure the rendered
mix with `npm run verify:delivery`; audition it before approval.

`npm run build` reports any missing file and records hashes in the ignored
`.build/media-audit.json`. `npm run build:proof` is explicitly a silent design
study and always uses reference graticules, even when some production files exist.
It cannot accidentally combine partial production assets with proof imagery.
