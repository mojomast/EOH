# Eye Of Hermes — Cartographer's field instrument

Design specification · 2026-11-06 · based on the locked v6 edit at `edcb673`.

## Idea

A survey plate brought to life. An engraved winged eye finds a meridian; the
meridian becomes the edge of a printed atlas plate. Every subsequent cut passes
through the same travelling survey head. The identity comes from **Fraunces,
oversized editorial hierarchy, orange drafting marks and disciplined paper
margins**, rather than optical effects. The captured map is the evidence.

## Palette and texture

| Token | Value | Use |
|---|---|---|
| Ink | `#0B0D0C` | Terminal, title cards, rules, body type |
| Bone | `#E9E4D8` | Atlas paper, reversed typography |
| Signal | `#FF5B1F` | Pen tip, cursor, active chapter, eye contour |
| Sea | `#486456` | Secondary annotation on paper; contrast-refined after still review |
| Pale sea | `#A2B7AA` | Secondary annotation on ink |

Orange is a large graphic accent, not small body copy on paper. Print grain is
static, seeded, 1.8% opacity. No gradients, glows, blur, glass, particles, floating
cards, decorative camera pushes or perpetual motion. The source capture retains
its original colours. Caldor and the control-path plate use the inverse ink stock.

## Type and grid

- **Fraunces variable**, OFL, bundled: `opsz=144, SOFT=0, WONK=1`, weight 600.
  Opening 152/0.92; closing 156/0.95; feature heads 64/1.01; architecture 112/0.98.
  Tracking −0.035em. Upright serif; orange emphasis is a small drafting rule.
- **IBM Plex Mono**, OFL, bundled, regular and medium: terminal 22/1.45;
  annotations 17–20/1.4; caption 27/1.35; register labels 15/1.3.
- 1920×1080, 30 fps. Safe horizontal margin 72 px. Header rule y=86.
  Left annotation column x=72, w=424. Gutter 40. Main plate x=536, w=1312.
  Map viewport y=174, h=738 (16:9), caption strip y=912, h=42.
  Captions y=982; footer y=1045. No text shares the map rectangle.
- Real footage uses `object-fit:contain`; never crop, recolour, zoom or mask away
  provider attribution. Frame and corner ticks sit **outside** the video.
- In settled feature scenes, authored overlay over map pixels is **0%**. The
  travelling transition head covers at most 6.5% of frame width, 9.5% of a feature
  map or 11.8% of the smaller opening map. Captions and terminal remain outside the map.

## Graphic grammar and truthfulness

Hairline rectangles, registration crosses, a compass index, printed folios,
latitude/longitude graticules and contour outlines of the existing winged-eye
mark. These contours are brand artwork, not invented terrain. Reference-location
coordinates are sourced in `locations.json`; they are not live camera telemetry.
Digits are revealed mechanically, always showing the correct digit (no fictitious
coordinates while counting). The tool total reveals/counts **11** without
overshoot. All folios refer to actual scene numbers; timecodes are actual film
time. Plate scale bars measure **normalised frame width, 0–100%**, explicitly
labelled `FRAME / %`; geographical scale remains the real capture's responsibility.

## Motion vocabulary

1. **Pen**: SVG `stroke-dashoffset` 1→0, 0.55–1.4 s; drawing precedes labels.
2. **Shutter**: title lines translate through fixed overflow masks, 0.65 s;
   plates/terminal rules reveal with `clip-path` or `scaleX`, never fade/scale-in.
3. **Survey sweep**: one 124px ruled orange/bone head travels across the frame
   over 0.56 s, centred on every existing cut. Its motion carries through the cut;
   it does not retime, freeze or cover the whole map.
4. **Index**: coordinate digits ratchet into place over 0.7 s; true final values
   remain held. Terminal uses exact original prompts, with an orange block cursor.
5. **Hold**: after a mark lands it stays put. Only the media, typing and true
   timeline progress continue. No ornamental idle loops.

Eases (local deterministic functions, no network plugins): `surveyOut(t) =
1-(1-t)^5` for shutters; `pen(t) = t*t*(3-2*t)` for drawn lines; linear sweep and
typing. The hero's physical register may settle by 3px, never its lettering or
numbers. All GSAP timelines are paused, registered and seekable. Readouts and
typing derive from absolute local time so reverse/random seeks cannot retain text.

## Locked edit and voice beat sheet

The 13 scene boundaries, source selects, local media offset 0, action times,
captions and Ash start times are frozen in `edit-lock.json`. Duration 112.318 s.
The media is supplied separately by the other agent. Cue boundaries below come
from the existing Ash captions; internal stressed-word placement is provisional
until the original WAVs are available to audition. Do not retime the WAVs to fit
the graphics. Typing starts at local 0.85 s and retains the original v6 duration
formula; it finishes at least 0.20 s before each recorded map action.

| Scene / absolute seconds | VO word or phrase anchor | Motion event |
|---|---|---|
| 01 · 0.000 / 2.397 / 3.784 | “Meet Eye of Hermes” / “Ask Hermes” / “See the world” | Wing contours plot and serif lockup registers; orange rule and first tagline wipe; second tagline wipes. |
| 02 · 5.967 / 8.567 | “A place” / original action cue | Paper plate opens; whole-earth reference ticks lock; completed prompt precedes action; original simulated reply types and holds. |
| 03 · 13.067 / 15.008 / 17.487 | “Tokyo” / “night vision” / “moves the globe” | Tokyo reference digits lock; night-vision caption cuts in; movement caption accompanies the result hold. Terminal instruction completes before 15.667. |
| 04 · 20.667 / 24.643 | “Flights, ships, satellites, and weather” / “San Francisco” | Feed caption strip reveals; Ferry Building coordinates resolve; location caption cuts in over the continuing capture. |
| 05 · 29.267 / 31.547 | “Pin” / “research, with sources” | Opera House reference locks and source strip wipes on; research caption accompanies the original map action. |
| 06 · 38.534 / 42.022 | “Director scene” / “Tokyo Tower” | Director heading shutters into place; approach/overhead caption cuts in. Original camera selects remain unchanged. |
| 07 · 46.801 / 50.277 / 53.601 | “Trace the Caldor Fire perimeter” / action cue / end of “before and after” | Inverse ink plate and orange register stroke; completed prompt's rule precedes the action; caption clears for the long comparison hold. |
| 08 · 60.268 / 63.135 | “public aircraft near Toronto Pearson” / action cue | Pearson reference locks; terminal command rule completes before the cue; capture does the following. |
| 09 · 68.135 / 71.127 / 74.185 | “Hollywood Park's racetrack” / action cue / “stadium that replaced it” | Paired plate ticks and caption strip frame the sequence; complete command precedes action; caption clears into result hold. |
| 10 · 79.135 / 82.468 / 87.885 | “Pin places near Richmond” / action cue / “overlays sidebar” | Richmond reference digits lock; command rule completes before map action; caption clears for the final sidebar hold. |
| 11 · 91.468 / 93.502 | “Hermes calls the plugin” / “local relay to the globe pane” | Engraved control schematic draws in causal order: Hermes → plugin → relay → GEV pane. |
| 12 · 98.868 / 101.118 | “Eleven composable tools” / “One conversation” | Oversized 11 registers, then PUBLIC / LIVE index strips reveal. |
| 13 · 104.568 / 105.931 / 108.948 | “Eye of Hermes” / “Hermes hackathon” / “GitHub” | Winged-eye contours close; large lockup lands; repository URL gets orange rule; v0.2.5 remains through final frame. |

## Review and research

First review six stills at **4.3, 11.9, 34.7, 55.0, 96.2, 110.2 s**, then render.
Check hierarchy, layout bounds, zero map occlusion, colour and consistent marks.
Review all twelve cuts at ±0.30 s, all nine prompt/action pairs, original select
order and end holds. Real-media picture and listening QC requires the handoff.

Current sources consulted on **2026-11-06** (npm latest is HyperFrames **0.9.19**):

- [GSAP animation contract](https://hyperframes.heygen.com/guides/gsap-animation):
  paused registered timelines, explicit endpoints, wrapper transforms, engine-owned media.
- [Motion grammar](https://hyperframes.heygen.com/prompting/motion): land and hold,
  carry a motif through cuts, overlap intentionally, no bouncing text or numbers.
- [Motion graphics](https://hyperframes.heygen.com/guides/motion-graphics): exact
  words/data, deliberate end states, short causal beats, review actual output.
- [Performance](https://hyperframes.heygen.com/guides/performance): avoid large
  blurs/filter stacks and per-frame layout reads; pre-bake static texture.

The implementation follows those technical practices with bespoke vector design,
not catalogue templates. Single-worker delivery is the reliable fallback.
