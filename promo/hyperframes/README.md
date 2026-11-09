# Eye Of Hermes — Cartographer's field instrument

**112.318-second locked edit · 1920×1080 · 30 fps · HyperFrames 0.9.19**

An ink-and-paper survey plate: Fraunces titles, engraved winged-eye contours,
orange drafting marks, a precise terminal and a single travelling graticule
transition. [STYLE.md](STYLE.md) is the design specification and voice beat sheet.

## Review this restyle

The source edit is PR #18, merged at `edcb673`. Its real footage and Ash WAVs were
not committed and are absent from this workstation. **The current MP4 and contact
sheet are a silent design proof**, using clearly identified mathematical reference
graticules. The other agent is supplying the existing final v6 media.

- Motion proof: `out/eoh-restyle-design-proof-1080p.mp4`
- Six design stills: `out/restyle-keyframes/`
- Committed contact sheet: [`review/contact-sheet.jpg`](review/contact-sheet.jpg)
- Verification and outstanding media QC: [`REVIEW.md`](REVIEW.md)

The final build requires the original media. It fails with an exact missing-file
list instead of silently exporting a substitute. Proofs build into `.proof/`;
delivery builds into `.build/`, so the two cannot be mistaken for each other.

## Run from `promo/hyperframes/`

Requirements: Node 22+, npm, FFmpeg, and the Chromium installed by HyperFrames.
Bundled fonts and locked dependencies make the film self-contained after install.

```sh
npm ci
npm run build:proof
npx hyperframes preview .proof --background --port 4334
npx hyperframes preview .proof --status
npx hyperframes check .proof
npm run qc:proof
npm run render:proof
node scripts/verify-render.mjs --proof
```

The Studio route is `http://localhost:4334/#project/.proof`. Stop it explicitly
with `npx hyperframes preview .proof --stop` after review.

### Original-media handoff and final render

1. Copy the **existing, final v6 selects**, already containing their prompt lead-in
   and result hold, into `footage/`. Copy the **unchanged Ash WAVs** into `vo/`.
   Exact names, scene starts and lengths are in [MANIFEST.md](MANIFEST.md).
2. Keep `captions.json`, `edit-lock.json`, source-file durations and trims intact.
   Caldor remains perimeter then before/after; SoFi remains before/after/split;
   the London and whole-earth takes retain their original action timing.
3. Run:

```sh
npm run build
npm run check
npx hyperframes snapshot .build --at 4.3,11.9,34.7,55,96.2,110.2 --no-end --output out/final-keyframes
npm run render
npm run verify:delivery
```

Final output: `out/eoh-restyle-1080p.mp4`. The delivery command deliberately uses
**one worker**, avoiding the earlier parallel-worker stalls. It preserves video
source offset 0, speed 1, original scene duration, Ash gain 1 and original audio
start times. No audio processing is applied to the source WAVs.

`verify:delivery` checks 1920×1080/30, 3,370 frames, audio presence, and integrated
loudness within 1.5 LU of −16 LUFS. At 30 fps the container is approximately
112.333 s: the final 15 ms is frame quantisation, not an editorial timing change.
Listen to the actual final output and check the captures' attribution, selects,
prompt/action sync and result holds. Those checks cannot be completed on a silent
design proof. `.build/media-audit.json` records SHA-256 hashes of all copied media.

## Editing map

| File | Responsibility |
|---|---|
| `STYLE.md` | Palette, type, grid, motion grammar, sourced research and beat sheet |
| `index.html` | Scene copy and unchanged prompts / timings |
| `style.css` | Paper/ink layout, typography, frames, terminal |
| `motion.js` | One survey sweep, film timecode, chapter progress |
| `scene-motion.js` | Pen/shutter motion, coordinate index, absolute-time typing |
| `scripts/motion-design.mjs` | Original vector eye, plate, terminal and graticule markup |
| `scripts/build.mjs` | Edit-lock checks, media integrity audit, sub-compositions |
| `scripts/qc.mjs` | Browser layout, original edit comparison, typing and shuffled-seek checks |
| `scripts/verify-render.mjs` | Encoded video and loudness checks |
| `locations.json` | Real reference coordinates with public citations |
| `edit-lock.json` / `captions.json` | Original v6 editorial contract / unchanged Ash captions |

Scene timelines use explicit GSAP endpoints and custom finite easing. Only local
time drives animation. The map window is fixed and uncovered; all labels,
captions and the terminal live in the margins. The 124px transition head covers
at most 9.5% of the feature map width while passing through, and 0% once settled.
Plate scale bars explicitly measure frame width (%), never an invented ground
distance. Reference coordinates describe the named location, not the moving camera.

The release total **11 tools** is verified against `plugin.yaml`; the end card
uses **v0.2.5** and **github.com/mojomast/EOH**. All generated output and source
media remain ignored. Font and artwork credits are in [LICENSES.md](LICENSES.md).
