import fs from 'node:fs';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
const proof=process.argv.includes('--proof');
const file=proof?'out/eoh-restyle-design-proof-1080p.mp4':'out/eoh-restyle-1080p.mp4';
const probe=JSON.parse(execFileSync('ffprobe',['-v','error','-show_streams','-show_format','-of','json',file],{encoding:'utf8'}));
const video=probe.streams.find(s=>s.codec_type==='video'),audio=probe.streams.find(s=>s.codec_type==='audio');
assert.equal(video.width,1920);assert.equal(video.height,1080);assert.equal(video.r_frame_rate,'30/1');
assert.equal(Number(video.nb_frames),3370);assert.ok(Math.abs(Number(video.duration)-112.318)<1/30);
let loudness=null;
if(proof)assert.ok(!audio,'The design proof is intentionally silent.');
else {
  assert.ok(audio,'Ash narration missing from delivery.');
}
// Measure only; FFmpeg reports on stderr, and never changes the source/output.
if(!proof) {
  const {spawnSync}=await import('node:child_process');
  const measured=spawnSync('ffmpeg',['-hide_banner','-i',file,'-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'],{encoding:'utf8',maxBuffer:8e6});
  assert.equal(measured.status,0);
  loudness=JSON.parse(measured.stderr.match(/\{\s*"input_i"[\s\S]*?\}/)?.[0]||'null');
  assert.ok(loudness,'No loudness measurement');
  assert.ok(Math.abs(Number(loudness.input_i)+16)<=1.5,`Integrated loudness ${loudness.input_i} LUFS; audition before delivery.`);
  assert.ok(Number(loudness.input_tp)<=0,'Clipping in final audio');
}
const report={file,proof,videoDuration:Number(video.duration),frames:Number(video.nb_frames),width:video.width,height:video.height,fps:video.r_frame_rate,bytes:fs.statSync(file).size,loudness};
fs.writeFileSync(`out/${proof?'proof':'delivery'}-encode-qc.json`,JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
