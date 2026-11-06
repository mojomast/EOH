import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { decorateScene, proofPlate, chapterRail, escapeHtml } from './motion-design.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const proof = process.argv.includes('--proof');
const build = path.join(root, proof ? '.proof' : '.build');
const lock = JSON.parse(fs.readFileSync(path.join(root, 'edit-lock.json')));
const locations = JSON.parse(fs.readFileSync(path.join(root, 'locations.json')));
const captionBytes = fs.readFileSync(path.join(root, 'captions.json'));
assert.equal(crypto.createHash('sha256').update(captionBytes).digest('hex'), lock.captionsSha256, 'The Ash caption timing/text is locked.');
let html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const scenePattern = /<section id="scene-(\d+)" class="([^"]+)" data-start="([\d.]+)" data-duration="([\d.]+)" data-media="([^"]+)">([\s\S]*?)<\/section>/g;
const scenes = [...html.matchAll(scenePattern)].map(m => ({id:m[1], classes:m[2], start:Number(m[3]), duration:Number(m[4]), name:m[5], body:m[6]}));
assert.equal(scenes.length, 13);
const missing = [];
for (const [i, scene] of scenes.entries()) {
  const original = lock.scenes[i];
  for (const key of ['id', 'start', 'duration', 'name']) assert.equal(scene[key], original[key], `${scene.id} ${key}: edit lock`);
  if (original.prompt) {
    assert.ok(scene.body.includes(`data-prompt="${original.prompt}"`), `${scene.id}: prompt changed`);
    assert.ok(scene.body.includes(`data-action-at="${original.actionAt}"`), `${scene.id}: action cue changed`);
  }
  if (original.reply) assert.ok(scene.body.includes(`data-reply="${original.reply}"`), 'Whole-earth reply changed');
  for (const file of [...(i < 10 ? [`footage/${scene.name}.mp4`] : []), `vo/${scene.name}.wav`]) {
    if (!fs.existsSync(path.join(root, file))) missing.push(file);
  }
}
if (!proof && missing.length) throw new Error(`Final render needs the original v6 media:\n${missing.join('\n')}\nUse npm run build:proof for the explicitly separate, silent design study.`);
fs.rmSync(build, {recursive:true, force:true});
fs.mkdirSync(path.join(build, 'compositions'), {recursive:true});
fs.mkdirSync(path.join(root, 'out'), {recursive:true});
fs.cpSync(path.join(root, 'assets'), path.join(build, 'assets'), {recursive:true});
fs.copyFileSync(path.join(root, 'node_modules/gsap/dist/gsap.min.js'), path.join(build, 'assets/gsap.min.js'));
for (const file of ['style.css', 'motion.js']) fs.copyFileSync(path.join(root, file), path.join(build, file));
const sceneMotion = fs.readFileSync(path.join(root, 'scene-motion.js'), 'utf8');
const audio = [];
const hashes = {};
function copyAsset(relative) {
  const source = path.join(root, relative), dest = path.join(build, relative);
  fs.mkdirSync(path.dirname(dest), {recursive:true});
  try { fs.linkSync(source, dest); } catch { fs.copyFileSync(source, dest); }
  hashes[relative] = crypto.createHash('sha256').update(fs.readFileSync(source)).digest('hex');
}
html = html.replace(scenePattern, (whole, number, classes, start, duration, name, body) => {
  const id = `scene-${number}`;
  body = decorateScene(body, number, locations[number]);
  if (body.includes('<!-- MEDIA -->')) {
    const video = `footage/${name}.mp4`;
    if (proof) body = body.replace('<!-- MEDIA -->', proofPlate());
    else {
      copyAsset(video);
      body = body.replace('<!-- MEDIA -->', `<video id="video-${number}" class="clip capture-media" data-start="0" data-duration="${duration}" data-track-index="2" src="${video}" muted playsinline></video>`);
    }
  }
  const composition = `<template>\n<section id="${id}" class="${classes}" data-composition-id="${id}" data-width="1920" data-height="1080">${body}</section>\n<script>\n${sceneMotion.replaceAll('__SCENE_ID__', id).replaceAll('__SCENE_DURATION__', duration)}\n</script>\n</template>\n`;
  fs.writeFileSync(path.join(build, 'compositions', `${id}.html`), composition);
  if (!proof) {
    const voice = `vo/${name}.wav`;
    copyAsset(voice);
    audio.push(`<audio id="voice-${number}" class="clip" data-start="${start}" data-duration="${duration}" data-track-index="4" data-volume="1" src="${voice}"></audio>`);
  }
  return `<div id="${id}" class="clip scene-host" data-stock="${classes.includes('inverse') ? 'ink' : 'paper'}" data-composition-id="${id}" data-composition-src="compositions/${id}.html" data-start="${start}" data-duration="${duration}" data-track-index="1" data-track-kind="graphics" data-width="1920" data-height="1080"></div>`;
});
const cues = JSON.parse(captionBytes).captions;
html = html.replace('<!-- CAPTIONS -->', cues.map(({start,end,text}, i) => `<p id="caption-${i+1}" class="clip caption-cue" data-start="${start}" data-duration="${end-start}" data-track-index="3" data-track-kind="captions">${escapeHtml(text)}</p>`).join('\n'));
html = html.replace('<!-- AUDIO -->', audio.join('\n')).replace('<!-- CHAPTER_RAIL -->', chapterRail(scenes));
fs.writeFileSync(path.join(build, 'index.html'), html);
fs.writeFileSync(path.join(build, 'media-audit.json'), JSON.stringify({proof, missing, hashes, duration:lock.duration}, null, 2));
console.log(`Built ${scenes.length} locked scenes in ${path.basename(build)}. ${proof ? 'SILENT DESIGN PROOF: reference graticules, no product captures or voiceover.' : 'Original footage and Ash WAVs copied byte-for-byte; timing unchanged.'}`);
