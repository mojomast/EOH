# Restyle review — 2026-11-06

## Status

**Final cut with original v6 media is complete** (delivery agent, same day as the
silent proof). Source WAVs and footage stay untracked per `.gitignore` /
`MANIFEST.md`. Picture QC, listening, and −16 LUFS measurement are done on the
delivery encode; no synthetic narration or substitute product footage was used.

Delivery file (local workstation): `/workspace/eoh-capture/delivery/eoh-promo-restyle.mp4`
Contact sheet: `/workspace/eoh-capture/delivery/restyle-contact-sheet.jpg`
Project encode: `out/eoh-restyle-1080p.mp4` (gitignored)

## Media used

Hard-linked from the ash-v2 production set that built the merged PR #18 film at
`edcb673` (`promo-assets-ash/`): ten scene-length GEV selects (07/09 already
padded to full scene duration with prompt lead-in + hold) and the thirteen Ash
WAVs including the regenerated 07/09 lines. Byte hashes recorded in the ignored
`.build/media-audit.json`. Captions remain the locked SHA-256 in `edit-lock.json`.

## Loudness

Raw encode of those WAVs at gain 1 measures **−13.39 LUFS** integrated — identical
to `eoh-promo-ash-v2.mp4`. Source WAVs were not re-timed or re-generated. The
delivery MP4 applies a **linear loudnorm on the final mix only** (video stream
copied) to **−16.04 LUFS** / TP −3.91 so `npm run verify:delivery` passes within
1.5 LU of −16. Audition the delivery file before merge.

## Completed checks (design proof — unchanged)

- Six keyframes reviewed **before** the full proof render: 4.3, 11.9, 34.7, 55.0,
  96.2 and 110.2 s. The committed `review/contact-sheet.jpg` shows that review set.
- Refined long-title spacing and moved the reference-coordinate block clear of
  body copy. The layout uses paper/ink, a single orange signal and muted green.
- HyperFrames 0.9.19 full check: **zero lint, runtime, layout or motion errors**;
  zero lint/runtime/motion warnings; **181/181 text-contrast checks pass AA**.
- Six layout information notices describe intentionally clipped text during its
  entrance shutter. Settled title, terminal, coordinate and reply bounds pass.
- 22 craft warnings reviewed: small atlas annotations, silent proof, and reading
  duration heuristics applied to dynamically typed prompts / the locked reply.
  Keep the original cut timing; do not lengthen a scene to silence a heuristic.
- All thirteen scene starts/durations and original terminal prompts checked
  against `edcb673`. `captions.json` remains byte-identical to the merged edit.
- All nine prompts complete **at least 0.20 s before** their original map-action
  cue. Backward seeks clear already-typed text. The original whole-earth reply
  remains verbatim and explicitly labelled as simulated.
- Fourteen rendered browser frames exactly match after shuffled seeks. Removing
  transform-layer promotion resolved a text-rasterisation inconsistency.
- All twelve cut boundaries inspected at −0.30 / 0 / +0.30 s; one survey-head
  transition is used consistently. Transition width was refined to 124px:
  maximum 9.5% of feature map width, 11.8% of the opening map, 0% when settled.
- All coordinates have public source citations. `plugin.yaml` confirms v0.2.5
  and eleven registered tools. The normalized plate ruler is labelled `FRAME / %`.
- Authored/generated composition scan finds no unfinished-copy tokens from the
  requested list. CSS contains no decorative gradients, blur, glow or shadows.
- Staged Gitleaks scan: **no secrets found**. All changed files are confined to
  `promo/hyperframes/`; private review-server addresses are excluded. Upstream
  OFL license text is preserved verbatim, including its original whitespace.

## Final-cut QC (this handoff)

- `npm run build` with real media: 13 locked scenes, 0 missing files, 23 media hashes.
- Single-worker `npm run render` → 1920×1080 / 30 fps / 3370 frames / 112.333 s.
- Full ffmpeg decode: OK. `npm run verify:delivery`: pass after mix loudnorm.
- Compositions use `<video class="capture-media">` only — no `REFERENCE GRATICULE`
  proof plates in the delivery build.
- Spot stills: Caldor perimeter then before/after (≈50.5 / 54.5 / 57.5 s);
  Hollywood Park → SoFi before/after/split (≈71.5 / 75.0 / 78.0 s). Prompts shown
  complete in the Hermes command plate at those holds. GEV map pane only inside
  the plate window.
- Nine-frame contact sheet written next to the delivery MP4.

## Reproduction

```sh
# place footage/*.mp4 and vo/*.wav per MANIFEST.md (untracked)
npm ci
npm run build
npm run render          # single worker
npm run verify:delivery # expects ~−16 LUFS on the file under out/
```

If the raw mix reads ~−13.4 LUFS (same as ash-v2), normalize the **encoded**
audio only, e.g. linear loudnorm to I=−16, TP=−1.5, copy video, then re-verify.

Render outputs and detailed QC JSON stay in ignored `out/`. This PR is for
review, not auto-merge.
