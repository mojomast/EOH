// stdin: {lat, lon, alt, heading, pitch, style, layers, base}; argv[2]: God's Eye View checkout.
// stdout: {url, code} — a /share/<code> link made with upstream's own share codec.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const load = (rel) => import(pathToFileURL(path.join(process.argv[2], rel)).href);
const codec = await load('src/share/viewCodec.js');
const links = await load('src/share/shareLinkPath.js');
const { createDefaultLayerState } = await load('src/data/layerState.js');
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const v = JSON.parse(raw || '{}');
const input = { lat: v.lat, lon: v.lon, alt: v.alt, heading: v.heading ?? 0, pitch: v.pitch ?? -90, sharedAtMs: Date.now() };
if (v.style) input.style = v.style;
if (Array.isArray(v.layers) && v.layers.length) {
  const layerState = createDefaultLayerState();
  layerState.enabledLayerIds = v.layers;
  input.layerState = layerState;
}
const code = codec.encodeShareView(input);
process.stdout.write(JSON.stringify({ code, url: links.shareLinkUrl(code, v.base.replace(/\/?$/, '/')) }));
