import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { decorateScene, placeholderInstrument, chapterRail } from './motion-design.mjs';

// Materialize the editable source into a disposable HyperFrames project.
// Real media is linked/copied only when it exists, so missing assets never
// prevent the scaffold from rendering and the source HTML stays unchanged.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const build = path.join(root, '.build');
const exists = relative => fs.existsSync(path.join(root, relative));
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[char]);
const copyAsset = relative => {
  const source = path.join(root, relative);
  const destination = path.join(build, relative);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  try { fs.linkSync(source, destination); } catch { fs.copyFileSync(source, destination); }
};

fs.rmSync(build, { recursive: true, force: true });
fs.mkdirSync(build, { recursive: true });
fs.mkdirSync(path.join(root, 'out'), { recursive: true });
fs.cpSync(path.join(root, 'assets'), path.join(build, 'assets'), { recursive: true });
fs.copyFileSync(path.join(root, 'node_modules/gsap/dist/gsap.min.js'), path.join(build, 'assets/gsap.min.js'));
for (const file of ['style.css', 'motion.js']) fs.copyFileSync(path.join(root, file), path.join(build, file));

let html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const compositionDuration = Number(/id="film"[^>]*data-duration="([\d.]+)"/.exec(html)?.[1]);
if (!Number.isFinite(compositionDuration)) throw new Error('Could not read composition data-duration');
const scenePattern = /<section id="scene-(\d+)"[^>]*data-start="([\d.]+)"[^>]*data-duration="([\d.]+)"[^>]*>([\s\S]*?)<\/section>/g;
const scenes = [...html.matchAll(scenePattern)].map(match => ({
  number: match[1], start: Number(match[2]), duration: Number(match[3]),
  name: match[4].match(/<!-- SLOT_MEDIA:([\w-]+) -->/)?.[1]
}));
if (scenes.length !== 13 || scenes.some(scene => !scene.name)) throw new Error('Expected thirteen scenes, each with a SLOT_MEDIA marker.');

const audio = [];
for (const scene of scenes) {
  const video = `footage/${scene.name}.mp4`;
  const shot = `shots/${scene.name}.png`;
  const voice = `vo/${scene.name}.wav`;
  let media = '';
  if (exists(video)) {
    copyAsset(video);
    media = `<video id="video-${scene.number}" class="clip slot-media" data-motion-blur="off" data-start="0" data-duration="${scene.duration}" data-track-index="2" src="${video}" muted playsinline></video>`;
  } else if (exists(shot)) {
    copyAsset(shot);
    media = `<img class="slot-media" data-motion-blur="off" src="${shot}" alt="${escapeHtml(scene.name)} screenshot">`;
  }
  html = html.replace(`<!-- SLOT_MEDIA:${scene.name} -->`, media || placeholderInstrument());
  if (exists(voice)) {
    copyAsset(voice);
    audio.push(`<audio id="voice-${scene.number}" class="clip" data-start="${scene.start}" data-duration="${scene.duration}" data-track-index="4" data-volume="1" src="${voice}"></audio>`);
    html = html.replace(`VO SLOT: ${voice}`, `VO: ${voice}`);
  }
}
// Each scene is mounted as a sub-composition. This keeps media timing local
// to its scene and avoids nesting a timed video inside a timed scene clip.
fs.mkdirSync(path.join(build, 'compositions'), { recursive: true });
const sceneMotion = fs.readFileSync(path.join(root, 'scene-motion.js'), 'utf8');
html = html.replace(scenePattern, (whole, number, start, duration, body) => {
  const id = `scene-${number}`;
  const feature = Number(number) >= 2 && Number(number) <= 10 ? ' feature' : '';
  const composition = `<template>\n<section id="${id}" class="scene${feature}" data-composition-id="${id}" data-width="1920" data-height="1080">${decorateScene(body, number)}</section>\n<script>\n${sceneMotion.replaceAll('__SCENE_ID__', id).replaceAll('__SCENE_DURATION__', duration)}\n</script>\n</template>\n`;
  fs.writeFileSync(path.join(build, 'compositions', `${id}.html`), composition);
  return `<div id="${id}" class="clip scene-host" data-composition-id="${id}" data-composition-src="compositions/${id}.html" data-start="${start}" data-duration="${duration}" data-track-index="1" data-track-kind="graphics" data-width="1920" data-height="1080"></div>`;
});
html = html.replace('<!-- VO_TRACKS -->', audio.join('\n  '));
html = html.replace('<!-- CHAPTER_RAIL -->', chapterRail(scenes));

if (exists('captions.json')) {
  const json = JSON.parse(fs.readFileSync(path.join(root, 'captions.json'), 'utf8'));
  const cues = Array.isArray(json) ? json : json.captions;
  if (!Array.isArray(cues)) throw new Error('captions.json must contain an array or an object with a captions array.');
  let lastEnd = 0;
  const captions = cues.map((cue, index) => {
    const { start, end, text } = cue;
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < lastEnd || end <= start || end > compositionDuration + 0.05 || typeof text !== 'string' || !text.trim()) {
      throw new Error(`Invalid caption ${index + 1}: use ordered, non-overlapping {start,end,text} cues within 0–${compositionDuration} seconds.`);
    }
    lastEnd = end;
    return `<p id="caption-${index + 1}" class="clip caption-cue" data-start="${start}" data-duration="${end - start}" data-track-index="3" data-track-kind="captions"><span>${escapeHtml(text)}</span></p>`;
  });
  html = html.replace('<!-- CAPTION_TRACK --><div class="caption-empty">SLOT: captions.json</div>', captions.join('\n'));
}
fs.writeFileSync(path.join(build, 'index.html'), html);
console.log(`Built ${scenes.length} scenes in .build. Video takes precedence over screenshots; missing media stays labeled.`);
