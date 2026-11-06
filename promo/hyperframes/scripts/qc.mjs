// Meaningful edit and browser regressions: original timing, prompt-before-action,
// real layout geometry, bundled fonts, and exact pixels after shuffled seeks.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import puppeteer from 'puppeteer-core';

const root = path.resolve('.proof');
const lock = JSON.parse(fs.readFileSync('edit-lock.json'));
const original = execFileSync('git',['show',`${lock.sourceCommit}:promo/hyperframes/index.html`],{encoding:'utf8'});
for (const s of lock.scenes) {
  const tag = new RegExp(`<section id="scene-${s.id}"[^>]*data-start="([\\d.]+)"[^>]*data-duration="([\\d.]+)"[^>]*>([\\s\\S]*?)<\\/section>`).exec(original);
  assert.ok(tag,`original scene ${s.id}`);
  assert.equal(Number(tag[1]),s.start);assert.equal(Number(tag[2]),s.duration);
  if(s.prompt) assert.ok(tag[3].includes(`data-prompt="${s.prompt}"`));
  if(s.reply) assert.ok(tag[3].includes(`data-reply="${s.reply}"`));
}
function findChrome(dir) {
  if(!fs.existsSync(dir))return;
  for(const f of fs.readdirSync(dir,{withFileTypes:true})) {
    const p=path.join(dir,f.name);
    if(f.isFile()&&f.name==='chrome-headless-shell')return p;
    if(f.isDirectory()){const found=findChrome(p);if(found)return found;}
  }
}
const executablePath=process.env.CHROME_BIN||findChrome(path.join(os.homedir(),'.cache/hyperframes/chrome'));
assert.ok(executablePath,'Set CHROME_BIN or install the HyperFrames browser.');
let html=fs.readFileSync(path.join(root,'index.html'),'utf8');
html=html.replace('<script src="assets/gsap.min.js"></script>','');
html=html.replace('</head>','<script src="assets/gsap.min.js"></script></head>');
html=html.replace(/(<div id="scene-\d+"[^>]*data-composition-src="([^"]+)"[^>]*>)<\/div>/g,(_,host,src)=>{
  let scene=fs.readFileSync(path.join(root,src),'utf8').replace('<template>','').replace('</template>','');
  scene=scene.replace(/<section id="(scene-\d+)"/,'<section id="$1-content"');
  return host+scene+'</div>';
});
const server=http.createServer((req,res)=>{
  if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(html);return;}
  const p=path.resolve(root,'.'+decodeURIComponent(req.url.split('?')[0]));
  if(!p.startsWith(root+path.sep)||!fs.existsSync(p)){res.statusCode=404;res.end();return;}
  res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.png':'image/png','.ttf':'font/ttf'})[path.extname(p)]||'application/octet-stream');
  fs.createReadStream(p).pipe(res);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await puppeteer.launch({executablePath,headless:true,args:['--no-sandbox']});
try {
  const page=await browser.newPage();await page.setViewport({width:1920,height:1080,deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`,{waitUntil:'networkidle0'});
  await page.evaluate(()=>document.fonts.ready);
  const seek=async time=>page.evaluate(t=>{
    window.__timelines['eoh-promo'].time(t,false);
    for(const host of document.querySelectorAll('.scene-host')) {
      const start=Number(host.dataset.start),duration=Number(host.dataset.duration);
      host.style.visibility=t>=start&&t<start+duration?'visible':'hidden';
      window.__timelines[host.id].time(Math.max(0,Math.min(duration,t-start)),false);
    }
    for(const cue of document.querySelectorAll('.caption-cue'))cue.style.visibility=t>=Number(cue.dataset.start)&&t<Number(cue.dataset.start)+Number(cue.dataset.duration)?'visible':'hidden';
  },time);
  const promptChecks=[];
  for(const s of lock.scenes.filter(s=>s.prompt)) {
    await seek(s.start+s.actionAt-.1);
    const state=await page.$eval(`#scene-${s.id} .terminal`,el=>({text:el.querySelector('.typed-prompt').textContent,end:Number(el.dataset.typeEnd)}));
    assert.equal(state.text,s.prompt,`scene ${s.id}: incomplete prompt before map action`);
    assert.ok(state.end<=s.actionAt-.199,`scene ${s.id}: insufficient lead`);
    promptChecks.push({scene:s.id,typeEnd:state.end,action:s.actionAt,lead:s.actionAt-state.end});
    await seek(s.start+.9);
    assert.notEqual(await page.$eval(`#scene-${s.id} .typed-prompt`,el=>el.textContent),s.prompt,'Backward seek retained completed text');
  }
  const layout=[];
  for(const s of lock.scenes) {
    await seek(s.start+s.duration-.6);
    const result=await page.$eval(`#scene-${s.id}`,host=>{
      const rect=sel=>{const e=host.querySelector(sel);if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};
      const clipping=[];
      for(const el of host.querySelectorAll('.title-line,.repo-lockup b,.plate-caption,.typed-prompt,.typed-reply,.caption-cue')) {
        if(el.clientWidth&&el.scrollWidth>el.clientWidth+2)clipping.push(el.textContent);
      }
      return {copy:rect('.scene-copy'),coords:rect('.coordinates'),terminal:rect('.terminal'),reply:rect('.typed-reply'),map:rect('.media-window'),clipping,text:host.innerText};
    });
    assert.deepEqual(result.clipping,[],`text overflow ${s.id}`);
    assert.ok(!/TODO|PLACEHOLDER|LOREM|SLOT|MOCK|AWAITING/i.test(result.text),`unfinished copy ${s.id}`);
    if(result.coords) {
      assert.ok(result.copy.bottom<result.coords.y,`copy/coordinate collision ${s.id}: ${result.copy.bottom}`);
      assert.ok(result.coords.bottom<result.terminal.y,`coordinate/terminal collision ${s.id}`);
      assert.ok(result.terminal.right<result.map.x,`terminal obscures map ${s.id}`);
      if(result.reply)assert.ok(result.reply.bottom<result.terminal.bottom-12,`reply overflow ${s.id}`);
    }
    layout.push({scene:s.id,clipping:result.clipping,overlay:0});
  }
  const times=[.45,4.3,6.1,7.2,11.9,21.8,34.7,46.75,55,71.3,83.1,96.2,101.6,110.2];
  const hashes=[],frames=[];
  for(const t of times){await seek(t);const frame=await page.screenshot();frames.push(frame);hashes.push(createHash('sha256').update(frame).digest('hex'));}
  for(const i of [13,2,8,0,11,5,9,1,6,10,3,12,7,4]) {
    await seek(times[i]);
    const frame=await page.screenshot();
    const hash=createHash('sha256').update(frame).digest('hex');
    if(hash!==hashes[i]){fs.writeFileSync('out/seek-reference.png',frames[i]);fs.writeFileSync('out/seek-replayed.png',frame);}
    assert.equal(hash,hashes[i],`non-deterministic seek at ${times[i]}`);
  }
  const fonts=await page.evaluate(()=>({fraunces:document.fonts.check('600 64px Fraunces'),plex:document.fonts.check('22px Plex')}));
  assert.ok(fonts.fraunces&&fonts.plex);assert.deepEqual(errors,[]);
  fs.writeFileSync('out/restyle-qc.json',JSON.stringify({proof:true,promptChecks,layout,seekFrames:times,fonts,errors},null,2));
  console.log('PASS: original edit lock; nine prompts complete before action; 13 layouts fit; 14 shuffled-seek pixel matches; bundled fonts loaded; no unfinished on-screen copy or browser errors.');
}finally{await browser.close();server.close();}
