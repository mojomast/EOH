# Restyle review — 2026-11-06

## Status

The deliverable available on this workstation is a **silent design proof** of the
complete 112.318-second edit. The original ten GEV MP4s and thirteen Ash WAVs are
being supplied by the other agent. Original-media picture QC, listening review
and final −16 LUFS measurement remain outstanding; no synthetic narration or
substitute product footage has been introduced.

## Completed checks

- Six keyframes reviewed **before** the full render: 4.3, 11.9, 34.7, 55.0,
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

## Reproduction and handoff

`npm run qc:proof` performs edit, browser-layout, typing, font and seek checks.
`npx hyperframes check .proof` performs the full framework check.
`node scripts/verify-render.mjs --proof` checks the encoded silent proof at
1920×1080, 30 fps, 3,370 frames / 112.333333 s. The 15ms difference from the
112.318-second composition is the final frame's rounding.

After copying original media per `MANIFEST.md`, run `npm run check`, render final
keyframes, `npm run render`, and `npm run verify:delivery`. Review the real Caldor
perimeter→comparison, Hollywood Park→SoFi→split, London and whole-earth selects,
map credits, prompt completion and final holds against the previous film.
The final-audio verifier measures the output without altering the WAVs.

Render outputs, detailed QC JSON and temporary transition sheets stay in ignored
`out/`. Only contact sheets are committed. This PR is for review, not auto-merge.
