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

Use the precise names in the manifest. Put recordings, stills, and voice tracks under `footage/`, `shots/`, and `vo/`. When both video and still exist, the video wins; if neither exists, the footage and still paths remain visible in the designed slot card. Assets are contained, rather than stretched or cropped, so preserve provider credits and record the intended pane as closely as possible. Trim every video and VO track to its scene duration, with picture and narration aligned to the start of the beat. A short clip cannot fill the remainder of its slot automatically. Voice tracks are mixed at unity gain; source footage is muted. The source files, `captions.json`, `.build/`, `node_modules/`, and `out/` are gitignored.

Captions are **not** filled from the draft: create `captions.json` after the actual narration is recorded and transcribed. The file is an object with a `captions` array, or the array itself. Start/end are absolute seconds from the beginning of the film; cues must be ordered, non-overlapping, nonempty, and within 0–89 seconds. See [`captions.example.json`](captions.example.json); it is illustrative and is not rendered until renamed. Cues are burned into the bottom caption band. With no file, `SLOT: captions.json` remains visible.

## Editing components

The timeline is explicit in `index.html`; `scripts/build.mjs` turns its scene sections into Studio scene hosts for preview. Shared styles live in `style.css`; deterministic, seekable motion lives in `motion.js` and `scene-motion.js`.

* **Feature title card:** `.scene-copy`, `.eyebrow`, heading, supporting paragraph, and `.side-note`. The `.enter` class applies the common entrance animation.
* **Lower third:** `.lower-third` with `.lower-number`, a `b` title, and a `small` label. Position is set in `style.css`.
* **Chat:** `.chat-bubble` accepts a JSON array of strings in `data-chat`, such as `data-chat='["Fly to Tokyo."]'`; `.chat-text` is typed by the seekable scene timeline. This is a shot-direction prompt, not an invented agent response.
* **Footage HUD:** `.hud-frame`, `.hud-top`, `.slot-visual`, and `.hud-bottom`. Each `.slot-visual` holds a unique `data-slot` and a matching `SLOT_MEDIA` marker consumed by the build.
* **Map callouts:** `.callout-ring` and `.callout-arrow`; adjust their CSS position to match the real footage. They are decorative and do not imply a real map location.
* **Captions:** `.caption-track` and generated `.caption-cue` elements. Change typography and placement in `style.css`, not in the recorded transcript.
* **Transitions:** `.transition-wipe` and `.transition-iris` are driven by the root GSAP timeline. Change scene-boundary times in `motion.js` if the timeline is retimed.
* **Counters:** on a `.counter`, replace both `data-count="{{PLACEHOLDER}}"` and its displayed text with a verified non-negative integer. The scene timeline will animate it; unfilled values never animate into invented numbers.

When retiming, update the section's `data-start`/`data-duration`, the corresponding transition boundary in `motion.js`, and the manifest. Ensure scenes are contiguous, caption/VO timings still match, and the root `data-duration` and the two timeline durations agree.

## Verification

`npm run lint` should report zero errors. The scaffold was rendered and checked at 89 seconds, 1920×1080, and 30 fps. A temporary test pattern, still, tone, and caption were used locally to exercise the media and audio paths and were removed afterward. Before recording, review the [license notes](LICENSES.md), provider attribution, and the actual state of live feeds.
