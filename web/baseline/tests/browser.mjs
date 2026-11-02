import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import midiPackage from '@tonejs/midi';
const { Midi } = midiPackage;
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const project = path.resolve(import.meta.dirname,'..');
await mkdir(path.join(project,'tmp'),{recursive:true});
const wav=await readFile(path.join(project,'dist/example.wav'));
const browser=await chromium.launch({channel:'msedge',headless:true,args:[
  '--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream',
  `--use-file-for-fake-audio-capture=${path.join(project,'dist/example.wav')}`]});
const context=await browser.newContext({viewport:{width:1360,height:1100},permissions:['microphone'],acceptDownloads:true});
const page=await context.newPage(), errors=[], requests=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>requests.push(request.url()));
let counter=0;
async function download(id,extension) {
  const pending=page.waitForEvent('download'); await page.locator(id).click();
  const file=await pending, destination=path.join(project,`tmp/export-${++counter}.${extension}`);
  await file.saveAs(destination); return readFile(destination);
}
async function readyInput() { await page.waitForFunction(()=>!document.getElementById('convert').disabled); }
async function convert() {
  await page.locator('#convert').click();
  await page.locator('#result-content').waitFor({state:'visible',timeout:90000});
  await page.waitForFunction(()=>!document.getElementById('variant').disabled);
}
async function exportPair() {
  const json=JSON.parse(await download('#download-json','json'));
  const midi=new Midi(await download('#download-midi','mid'));
  const notes=midi.tracks.flatMap(track=>track.notes);
  assert.equal(notes.length,json.notes.length);
  assert.equal(midi.tracks[0].instrument.number,json.playback.gmProgramNumber);
  assert.equal(json.tempo.bpm,null); assert.equal(json.tempo.quantized,false);
  assert.equal(json.timebase.origin,'original_audio_start');
  const tolerance=json.midiExport.timeRoundingToleranceSec+1e-9;
  json.notes.forEach((note,index)=>{
    assert.equal(notes[index].midi,note.pitchMidi);
    assert(Math.abs(notes[index].time-note.startSec)<=tolerance);
    assert(Math.abs(notes[index].time+notes[index].duration-note.startSec-note.durationSec)<=tolerance);
  });
  assert.equal(await page.locator('#note-count').textContent(),`${notes.length} 个音符`);
  return json;
}
try {
  await page.goto('http://127.0.0.1:4173/');
  await page.waitForFunction(()=>document.getElementById('model-badge').classList.contains('ready'),null,{timeout:60000});
  await page.locator('#example').click(); await readyInput();
  assert.deepEqual(await download('#download-original','wav'),wav,'original audio bytes must not change');
  assert.equal(await page.locator('#quality-panel').getAttribute('data-quality'),'ok');
  await convert();
  const first=await exportPair();
  assert(first.notes.length>0); assert.equal(first.selectedResult,'cleaned');
  assert.equal(first.input.sampleRateHz,22050); assert.equal(first.input.channels,1);
  const expectedPitches=[60,62,64,67];
  const matchedPitches=expectedPitches.filter(p=>first.results.raw.notes.some(n=>n.pitchMidi===p));
  assert(matchedPitches.length>=3,'recover most distinct synthetic pitches');
  await page.locator('#play-midi').click();
  await page.waitForFunction(()=>document.getElementById('play-midi').textContent.includes('停止'));
  await page.locator('#variant').selectOption('raw');
  assert(!(await page.locator('#play-midi').textContent()).includes('停止'),'changing output stops stale playback');
  const raw=await exportPair(); assert.equal(raw.selectedResult,'raw');
  assert.deepEqual(raw.notes,raw.results.raw.notes);
  await page.locator('#timbre').selectOption('bright');
  const bright=await exportPair(); assert.equal(bright.playback.gmProgramNumber,81);
  await page.locator('#timbre').selectOption('pluck');
  const pluck=await exportPair(); assert.equal(pluck.playback.gmProgramNumber,24);
  await page.locator('#variant').selectOption('cleaned');
  await page.locator('#trim').check();
  assert.equal(await page.locator('#result-content').isVisible(),false,'preprocessing change invalidates old exports');
  assert.equal(await page.locator('#download-json').getAttribute('href'),null);
  await convert();
  const trimmed=await exportPair();
  assert(trimmed.input.processing.trimStartSec>0);
  assert(trimmed.notes.every(n=>n.startSec>=trimmed.input.processing.trimStartSec));
  assert(trimmed.results.raw.notes.some(n=>n.pitchMidi===60 && n.startSec>.15 && n.startSec<.5),'trimmed recognition maps back to original first-note time');
  assert.deepEqual(await download('#download-original','wav'),wav);
  await page.locator('#quality-panel details').evaluate(element=>{element.open=true;});
  await page.screenshot({path:path.join(project,'tmp/desktop-v2.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(project,'tmp/mobile-v2.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile layout overflow');
  await page.setViewportSize({width:1360,height:1100});
  await page.locator('#record').click();
  await page.waitForFunction(()=>document.getElementById('record-rings').classList.contains('recording'));
  await page.waitForTimeout(4600); await page.locator('#record').click(); await readyInput();
  assert.equal(await page.locator('#result-content').isVisible(),false);
  await convert(); const recording=await exportPair();
  assert.equal(recording.source.kind,'recording');
  assert(recording.notes.length>0);
  assert((await download('#download-original','webm')).length>100);
  const stereo=Buffer.alloc(44+(wav.length-44)*2);
  wav.copy(stereo,0,0,44);
  stereo.writeUInt32LE(stereo.length-8,4); stereo.writeUInt16LE(2,22);
  stereo.writeUInt32LE(22050*4,28); stereo.writeUInt16LE(4,32); stereo.writeUInt32LE(stereo.length-44,40);
  for(let i=44;i<wav.length;i+=2) {
    const offset=44+(i-44)*2;
    stereo.writeInt16LE(wav.readInt16LE(i),offset); stereo.writeInt16LE(wav.readInt16LE(i),offset+2);
  }
  await page.locator('#file').setInputFiles({name:'stereo.wav',mimeType:'audio/wav',buffer:stereo}); await readyInput();
  await convert(); const stereoResult=await exportPair();
  assert.equal(stereoResult.source.decodedChannels,2);
  assert.equal(stereoResult.input.channels,1); assert.equal(stereoResult.input.sampleRateHz,22050);
  assert.deepEqual(await download('#download-original','wav'),stereo);
  const quiet=Buffer.from(wav);
  for(let i=44;i<quiet.length;i+=2) quiet.writeInt16LE(Math.round(quiet.readInt16LE(i)*.03),i);
  await page.locator('#file').setInputFiles({name:'quiet.wav',mimeType:'audio/wav',buffer:quiet}); await readyInput();
  assert.equal(await page.locator('#quality-panel').getAttribute('data-quality'),'warning');
  assert((await page.locator('#quality-summary').textContent()).includes('偏小'));
  await page.locator('#normalize').uncheck();
  assert((await page.locator('#processing-summary').textContent()).includes('×1.00'));
  const clipped=Buffer.from(wav);
  // A 220 Hz clipped waveform, not an alternating Nyquist signal removed by resampling.
  for(let i=44;i<clipped.length;i+=2) clipped.writeInt16LE(Math.sin(2*Math.PI*220*((i-44)/2)/22050)>=0?32767:-32768,i);
  await page.locator('#file').setInputFiles({name:'clipped.wav',mimeType:'audio/wav',buffer:clipped}); await readyInput();
  assert((await page.locator('#quality-summary').textContent()).includes('失真'));
  const silent=Buffer.from(wav); silent.fill(0,44);
  await page.locator('#file').setInputFiles({name:'silence.wav',mimeType:'audio/wav',buffer:silent});
  await page.waitForFunction(()=>document.getElementById('quality-panel').dataset.quality==='rejected');
  assert(await page.locator('#convert').isDisabled());
  assert.equal(await page.locator('#result-content').isVisible(),false);
  assert.equal(await page.locator('#download-json').getAttribute('href'),null);
  assert.deepEqual(await download('#download-original','wav'),silent);
  await page.locator('#file').setInputFiles({name:'broken.wav',mimeType:'audio/wav',buffer:Buffer.from('invalid audio')});
  await page.waitForFunction(()=>!document.getElementById('import').disabled);
  assert(await page.locator('#convert').isDisabled());
  assert.equal(await page.locator('#original-track').isVisible(),false);
  assert.equal(errors.length,0,errors.join('\n'));
  assert(requests.every(url=>url.startsWith('http://127.0.0.1:4173/')||url.startsWith('blob:')),'unexpected network requests');
  const summary={expectedPitches,matchedPitches,rawNotes:first.results.raw.notes.length,
    cleanedNotes:first.results.cleaned.notes.length,cleanupEdits:first.results.cleaned.edits,
    trimmedRawNotes:trimmed.results.raw.notes.length,trimmedCleanedNotes:trimmed.notes.length,
    trimStartSec:trimmed.input.processing.trimStartSec,recordedNotes:recording.notes.length,
    inputQualityCases:['quiet','possible_clipping','near_silence','invalid_file'],
    originalBytesPreserved:true,stereoToMono:true,exportsConsistent:true,browserErrors:errors,mobileNoOverflow:true,
    noExternalAudioRequests:true,limitation:'Synthetic waveform and simulated microphone; not a human humming accuracy study.'};
  await writeFile(path.join(project,'tmp/verification-v2.json'),JSON.stringify(summary,null,2));
  console.log(JSON.stringify(summary,null,2));
} finally {await browser.close();}
