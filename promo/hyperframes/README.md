# Eye Of Hermes — HyperFrames promo scaffold

An editable **89-second, 1920×1080, 30 fps** production scaffold. It has 13 timed scenes, designed graphics, a typed draft prompt, a live-control architecture diagram, and clearly marked missing-media cards. It contains **no recorded product footage, voiceover, music, or verified project statistics**. The `11 registered tools` in the architecture and outro is documented in the repository's `plugin.yaml` and README; the three stat-card values deliberately remain `{{PLACEHOLDER}}`.

## Preview and render

Prerequisites: Node.js **22 or newer**, npm, FFmpeg, and a Chromium-compatible browser. HyperFrames can install its supported browser if needed. From this directory:

```sh
npm ci
npm run preview
```

Open the Studio URL printed by the command (normally `http://localhost:3002/#project/.build`). Preview and scrub the timeline there. Studio is viewing the generated `.build/` project: edits made there are temporary and will be overwritten on the next build. Make lasting edits in the source files listed below. Stop the foreground server with Ctrl-C. To run it in the background, use `npm run preview -- --background --port 4321` and stop it with `npx hyperframes preview .build --stop`.

```sh
npm run lint             # build and check the composition structure
npm run check            # deeper HyperFrames checks
npm run render           # draft MP4: out/scaffold-preview.mp4
```

The render is intentionally gitignored. For a higher-quality render after supplying media, run:

```sh
npm run build
npx hyperframes render .build --fps 30 --quality delivery --output out/eoh-final.mp4
```

`npm run build` reads the editable `index.html`, generates one HyperFrames sub-composition per scene in `.build/compositions/`, and copies or hard-links available media into the disposable `.build/` project. This makes each video clip's time local to its own scene. Do not edit `.build/`: it is regenerated. **Re-run the build or restart preview whenever source or slot files are added or changed.** The source and output directories are not modified by the build except for creation of `out/`.

## Fill the slots

The complete shot list, exact proposed Hermes prompts, durations, and **DRAFT** voice scripts are in [`MANIFEST.md`](MANIFEST.md). Every scene has three named slots:

```text
footage/04-feeds.mp4       # H.264 MP4, 30 fps; fill the full 7-second beat
shots/04-feeds.png         # optional still if no video is available
vo/04-feeds.wav            # PCM WAV, 48 kHz, starts at 00:21
captions.json              # optional, global, absolute film time
```

Use the precise names in the manifest. Put recordings, stills, and voice tracks under `footage/`, `shots/`, and `vo/`. When both video and still exist, the video wins; if neither exists, the footage and still paths remain visible in the designed slot card. **Record footage as the God's Eye View map pane only** (crop out Hermes chat); target 1920×1080 or the native pane aspect. Scenes 02–10 show a **mock Hermes terminal** beside that footage — the terminal is generated chrome, not a screen capture. Assets use `object-fit: contain`, so preserve provider credits and match the pane framing. Trim every video and VO track to its scene duration, with picture and narration aligned to the start of the beat. A short clip cannot fill the remainder of its slot automatically. Voice tracks are mixed at unity gain; source footage is muted. The source files, `captions.json`, `.build/`, `node_modules/`, and `out/` are gitignored.

Captions are **not** filled from the draft: create `captions.json` after the actual narration is recorded and transcribed. The file is an object with a `captions` array, or the array itself. Start/end are absolute seconds from the beginning of the film; cues must be ordered, non-overlapping, nonempty, and within 0–89 seconds. See [`captions.example.json`](captions.example.json); it is illustrative and is not rendered until renamed. Cues are burned into the bottom caption band. With no file, `SLOT: captions.json` remains visible.

## Editing components

The timeline is explicit in `index.html`; `scripts/build.mjs` turns its scene sections into Studio scene hosts for preview. Shared styles live in `style.css`; deterministic, seekable motion lives in `motion.js` and `scene-motion.js`.

* **Feature title card:** `.scene-copy`, `.eyebrow`, heading, supporting paragraph, and `.side-note`. The build wraps heading words in `.word-mask` / `.title-word` spans for staggered perspective roll-ups; edit the plain heading in `index.html`.
* **Lower third:** `.lower-third` with `.lower-number`, a `b` title, and a `small` label. Position is set in `style.css`.
* **Mock Hermes terminal:** In source, place `<div data-mock-hermes-chat data-label="01" data-prompt="Fly to Tokyo."></div>` inside a `.split-display`. `scripts/motion-design.mjs` → `mockHermesChat()` expands it at build time into dark-mode Hermes chat chrome; `.chat-text` is typed by the seekable scene timeline from `data-chat`. Scenes **02–10** use this pattern. It is a shot-direction prompt overlay, not a live capture or invented agent response.
* **Footage HUD:** `.hud-frame`, `.hud-top`, `.slot-visual`, and `.hud-bottom` hold the **GEV map-pane** media. Each `.slot-visual` holds a unique `data-slot` and a matching `SLOT_MEDIA` marker consumed by the build. Pair with the mock terminal via `.split-display`.
* **Map callouts:** `.callout-ring` and `.callout-arrow`; adjust their CSS position to match the real footage. They are decorative and do not imply a real map location.
* **Captions:** `.caption-track` and generated `.caption-cue` elements. Change typography and placement in `style.css`, not in the recorded transcript.
* **Transitions:** diagonal `.transition-slice`, split `.transition-shutter`, and circular `.transition-iris` are driven by the root GSAP timeline. Their timing and the segmented chapter-progress rail are derived from the generated scene hosts, so they follow the section timing in `index.html`.
* **Counters:** on a `.counter`, replace both `data-count="{{PLACEHOLDER}}"` and its displayed text with a verified non-negative integer. The scene timeline will animate it; unfilled values never animate into invented numbers.

When retiming, update the section's `data-start`/`data-duration` and the manifest. Ensure scenes are contiguous, caption/VO timings still match, the root `data-duration` matches the total, and the scene-local animation fits each beat. The current caption validator caps cues at 89 seconds; update that cap if the film length changes.

## Motion direction

The optical-instrument treatment layers several independently timed planes:

* **Kinetic typography:** clipped per-word reveals, perspective settling, and delayed supporting copy keep the headline first in the reading order.
* **Hero optics:** original SVG tick marks, drawn-on paths, counter-rotating rings, and a gimbal frame the opening and closing brand lockups. Markup comes from `scripts/motion-design.mjs`.
* **HUD choreography:** directional clip reveals, shallow 3D entrances, traced highlight edges, a flipping chapter badge, and staggered interface labels. The panels settle to a flat, readable media frame.
* **Ambient depth:** slow light-field drift, oversized outlined chapter numerals, and scanning instruments behind empty slots. The scan/reticle is omitted when real media is supplied.
* **Architecture flow:** nodes enter in sequence, followed by two finite packet passes and timed surface highlights. This is an animated schematic of the documented control path.
* **Cut design:** opaque shutters, diagonal panels, and an iris cover each scene boundary before opening onto the next shot. The chapter rail tracks actual film time.

Motion uses paused, finite GSAP timelines registered with HyperFrames. There are no CSS animation clocks, random particles, or independent animation loops. The root opts into HyperFrames' native **180° shutter / 12-sample motion blur**, explicitly using the portable `filter` tier; it approximates rotational blur and works without HTML-in-Canvas support. Headings, captions, and inserted media opt out to preserve readability. For a sharp-motion export, pass `--page-motion-blur off` to `hyperframes render`.

Adjust entrance distances, stagger, and eases in `scene-motion.js`; adjust cut styles in `motion.js`. Decorative color, opacity, geometry, and masking live in `style.css`. Final footage retains `object-fit: contain`, scene-local playback, and its original full frame after the entrance settles.

## Verification

`npm run lint` should report zero errors. The scaffold was rendered and checked at 89 seconds, 1920×1080, and 30 fps. A temporary test pattern, still, tone, and caption were used locally to exercise the media and audio paths and were removed afterward. Before recording, review the [license notes](LICENSES.md), provider attribution, and the actual state of live feeds.
