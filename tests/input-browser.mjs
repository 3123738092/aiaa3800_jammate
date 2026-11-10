import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const {Midi}=createRequire(new URL('../web/optimized/package.json', import.meta.url))('@tonejs/midi');
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const root=path.resolve(import.meta.dirname,'..');
await mkdir(path.join(root,'tmp/browser'),{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({acceptDownloads:true,viewport:{width:1360,height:1000}});
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const base=process.env.INPUT_LAB_URL||'http://127.0.0.1:4188';const report={versions:{}};
async function download(id,destination){const next=page.waitForEvent('download');await page.locator(id).click();await(await next).saveAs(destination);return readFile(destination);}
async function checkBand(kind, sample = kind) {
  const output = path.join(root, 'tmp/browser', sample);
  const event = JSON.parse(await download('[data-event]', output + '.agent.json'));
  const rhythm = kind === 'rhythm';
  const mode = rhythm ? await page.locator('#rhythm-mode').inputValue() : null;
  const received = page.waitForResponse(r => r.url() === base + (rhythm ? '/api/reference/rhythm/create' : '/api/create'));
  await page.locator('[data-render]').click();
  const response = await received;
  const result = await response.json();
  assert.equal(response.status(), 200, result.error);
  const request = response.request().postDataJSON();
  assert.deepEqual(rhythm ? request.event : request, event);
  assert.equal(result.event_id, event.event_id);
  if (rhythm) {
    assert.equal(result.mode, mode);
    assert.match(result.reference_commit, /^a63a0f5/);
  } else {
    assert.equal(result.plan.strategy, 'RESPOND');
    assert.deepEqual(result.plan.trigger_event_ids,[event.event_id]);
    assert.deepEqual(result.warnings, []);
  }
  await page.locator('[data-output]').waitFor({state:'visible'});
  const wav = await download('[data-wav]', output + '.band.wav');
  assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
  const audio = await page.locator('[data-output] audio').evaluate(async player => {
    const context = new AudioContext();
    try {
      const buffer = await context.decodeAudioData(await (await fetch(player.src)).arrayBuffer());
      const samples = buffer.getChannelData(0);
      return {duration:buffer.duration, rms:Math.sqrt(samples.reduce((sum,x) => sum+x*x,0)/samples.length)};
    } finally { await context.close(); }
  });
  assert(audio.duration >= result.bars * 4 * 60 / result.tempo_bpm - .01);
  assert(audio.rms > .001);
  const midi = new Midi(await download('[data-midi]', output + '.band.mid'));
  assert(midi.tracks.filter(t => t.notes.length).length >= 3);
  if (kind === 'humming') {
    const lead = midi.tracks.find(t => t.channel === 4 || t.name === 'lead');
    const notes = event.payload.melody.notes;
    assert(lead.notes.length > 0);
    assert.notDeepEqual(lead.notes.map(n=>n.midi), notes.map(n=>n.pitchMidi),'the lead varies the input melody');
    if (notes.length >= 5) {
      const matchedGaps = lead.notes.slice(1).filter((n,i) => i+1 < notes.length &&
        Math.abs(n.time-lead.notes[i].time-(notes[i+1].startSec-notes[i].startSec)) < .003);
      assert(matchedGaps.length >= 2,'the answer retains part of the source rhythm');
    }
  } else {
    const bass = midi.tracks.find(t => t.channel === 2 || t.name === 'bass');
    const firstBar = bass.notes.filter(n => n.ticks < midi.header.ppq * 4);
    const hits = firstBar.map(n=>n.ticks/midi.header.ppq);
    const anchors = event.payload.rhythm.onset_beats;
    if (mode === 'simplify') {
      assert(hits.every(beat => anchors.includes(beat)) && hits.length < anchors.length);
    } else {
      assert(anchors.every(beat => hits.includes(beat)), 'input anchors survive');
      if (mode === 'follow') assert.deepEqual(hits, anchors);
      else assert(hits.length > anchors.length, 'the arrangement adds rhythmic detail');
    }
    assert(Math.abs(result.tempo_bpm-event.payload.rhythm.tempo_bpm)<.001);
  }
  const {wav:unusedWav, midi:unusedMidi, ...summary} = result;
  await writeFile(output + '.plan.json', JSON.stringify(result.plan ?? summary,null,2));
  return {...audio, renderer:summary.renderer, bars:summary.bars, tempo:summary.tempo_bpm};
}
try{
  for(const version of (process.env.INPUT_VERSIONS||'baseline,optimized').split(',')){
    await page.goto(base);
    await page.locator(`a[href="${version}/dist/"]`).click();
    await page.waitForFunction(()=>document.querySelector('#model-badge.ready'),null,{timeout:90000});
    await page.locator('#example').click();await page.waitForFunction(()=>!document.querySelector('#convert').disabled);
    await page.locator('#convert').click();await page.locator('#result-content').waitFor({state:'visible',timeout:90000});
    const event=JSON.parse(await download('#download-event',path.join(root,`tmp/browser/${version}.event.json`)));
    const doc=JSON.parse(await download('#download-json',path.join(root,`tmp/browser/${version}.melody.json`)));
    const midi=new Midi(await download('#download-midi',path.join(root,`tmp/browser/${version}.mid`)));
    assert.equal(event.source,'humming');assert.equal(event.payload.notes.length,doc.notes.length);
    assert.equal(midi.tracks[0].notes.length,event.payload.notes.length);
    midi.tracks[0].notes.forEach((n,i)=>{assert.equal(n.midi,event.payload.notes[i].pitch_midi);assert(Math.abs(n.time-event.payload.notes[i].start_sec)<.0011);});
    const validation=await context.request.post(`${base}/api/validate`,{data:event});assert.equal(validation.status(),200,await validation.text());
    await page.screenshot({path:path.join(root,`tmp/browser/${version}.png`),fullPage:true});
    await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow');
    await page.setViewportSize({width:1360,height:1000});
    report.versions[version]={raw:doc.results.raw.notes.length,selected:doc.notes.length,eventValid:true,midiConsistent:true};
    if(version==='optimized'){
      report.hummingBand = await checkBand('humming');
      const bandAudio = page.locator('#band-panel audio');
      const preview = page.locator('#play-midi');
      await bandAudio.evaluate(a => a.play());
      await preview.click();
      await page.waitForFunction(()=>document.querySelector('#play-midi').textContent.includes('停止'));
      assert(await bandAudio.evaluate(a=>a.paused), 'MIDI preview stops band playback');
      await bandAudio.evaluate(a => a.play());
      await page.waitForFunction(()=>document.querySelector('#play-midi').textContent.includes('听 MIDI'));
      await page.locator('#original').evaluate(a=>a.play());
      assert(await bandAudio.evaluate(a=>a.paused), 'original stops band playback');
      await bandAudio.evaluate(a => a.play());
      assert(await page.locator('#original').evaluate(a=>a.paused), 'band stops original playback');
      await bandAudio.evaluate(a => a.pause());
      const bandWav = await page.locator('[data-wav]').getAttribute('href');
      await page.selectOption('#timbre','bright');
      assert(await page.locator('[data-wav]').isVisible());
      assert.equal(await page.locator('[data-wav]').getAttribute('href'), bandWav);
      await page.selectOption('#variant','raw');
      assert(await page.locator('[data-output]').isHidden(), 'changing the notes invalidates the old band');
      await page.selectOption('#variant','cleaned');
      assert.deepEqual(doc.notes.map(n=>n.pitchMidi),[60,64,67,64,62],'recover all five expected pitches');
      assert.equal(doc.model.executionBackend,'cpu');
      await page.locator('#convert').click();await page.locator('#result-content').waitFor({state:'visible',timeout:90000});
      const repeated=JSON.parse(await download('#download-json',path.join(root,'tmp/browser/repeated.melody.json')));
      assert.deepEqual(repeated.notes,doc.notes,'repeat conversion is deterministic');
      assert.equal(repeated.source.sessionId,event.session_id,'reprocessing retains clip session');
      report.versions[version].repeatable=true;
      await page.locator('#file').setInputFiles(path.join(root,'web/fixtures/sustain.wav'));
      await page.waitForFunction(()=>!document.querySelector('#convert').disabled);
      assert(await page.locator('[data-output]').isHidden());
      await page.locator('#convert').click();
      await page.locator('#band-panel').waitFor({state:'visible',timeout:90000});
      report.uploadedSustain = await checkBand('humming', 'uploaded-sustain');
      const uploaded = JSON.parse(await readFile(path.join(root,'tmp/browser/uploaded-sustain.agent.json')));
      assert.deepEqual(uploaded.payload.melody.notes.map(n=>n.pitchMidi), [60]);
      assert(uploaded.payload.melody.notes[0].durationSec > 3);
    }
    console.log(version,report.versions[version]);
  }
  await page.goto(base);await page.locator('a[href="rhythm.html"]').click();
  assert(await page.locator('[data-output]').isHidden(),'switching from humming starts with no result');
  await page.locator('#example').click();await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);
  await page.locator('#analyze').click();await page.locator('#result').waitFor({state:'visible',timeout:90000});
  const event=JSON.parse(await download('[data-event]',path.join(root,'tmp/browser/rhythm.event.json')));
  assert.equal(event.event_type,'rhythm_seed');assert.deepEqual(event.payload.rhythm.onset_beats,[0,1,1.5,3]);
  await page.route('**/api/reference/rhythm/create', route => route.fulfill({
    status:503, contentType:'application/json', body:JSON.stringify({error:'测试：渲染暂不可用'})
  }), {times:1});
  await page.click('[data-render]');
  await page.waitForFunction(()=>document.querySelector('[data-status]').textContent.includes('渲染暂不可用'));
  assert(await page.locator('[data-render]').isEnabled(), 'failed renders can be retried');
  report.rhythmBand = await checkBand('rhythm');
  await page.selectOption('#rhythm-mode', 'follow');
  report.rhythmFollow = await checkBand('rhythm', 'rhythm-follow');
  await page.selectOption('#rhythm-mode', 'simplify');
  report.rhythmSimplify = await checkBand('rhythm', 'rhythm-simplify');
  await page.selectOption('#rhythm-mode', 'vary');
  await page.locator('#example').click();await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);
  await page.locator('#analyze').click();await page.locator('#result').waitFor({state:'visible',timeout:90000});
  const nextEvent=JSON.parse(await download('[data-event]',path.join(root,'tmp/browser/rhythm-next.event.json')));
  assert.notEqual(nextEvent.session_id,event.session_id,'independent clips use distinct sessions');
  // A faster uploaded WAV must set the new session's tempo from its own count-in.
  const faster = await readFile(path.join(root, 'web/rhythm-example.wav'));
  const hz = Math.round(faster.readUInt32LE(24) * 1.2);
  faster.writeUInt32LE(hz, 24);
  faster.writeUInt32LE(hz * faster.readUInt16LE(32), 28);
  await page.locator('#file').setInputFiles({name:'faster.wav', mimeType:'audio/wav', buffer:faster});
  await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);
  assert.equal(await page.locator('#download-original').getAttribute('download'),'faster.wav');
  assert(await page.locator('[data-output]').isHidden());
  await page.locator('#analyze').click();
  await page.locator('#band-panel').waitFor({state:'visible',timeout:90000});
  report.uploadedRhythm = await checkBand('rhythm', 'uploaded-rhythm');
  assert(Math.abs(report.uploadedRhythm.tempo - 120) < 3);
  report.rhythm={bpm:event.payload.rhythm.tempo_bpm,onsets:event.payload.rhythm.onset_beats};
  report.rhythm.independentClipSessions=true;
  await page.goto(`${base}/appendix.html`);assert.equal(await page.locator('#scores tr').count(),5);
  assert.equal(await page.locator('#rolls svg').count(),3);await page.locator('#case').selectOption('short');
  assert.match(await page.locator('#comparison').innerText(),/优化 3 音/);
  await page.locator('#case').selectOption('sustain');await page.screenshot({path:path.join(root,'tmp/browser/appendix.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'appendix mobile overflow');
  report.appendix={cases:5,comparisonSwitchWorks:true,mobileOverflow:false};
  assert.deepEqual(errors,[]);report.pageErrors=errors;
  await writeFile(path.join(root,'tmp/browser/report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
