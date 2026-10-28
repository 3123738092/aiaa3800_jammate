import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import midiPackage from '@tonejs/midi';
const {Midi}=midiPackage;
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const root=path.resolve(import.meta.dirname,'..');
await mkdir(path.join(root,'tmp/browser'),{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({acceptDownloads:true,viewport:{width:1360,height:1000}});
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const base=process.env.INPUT_LAB_URL||'http://127.0.0.1:4175';const report={versions:{}};
async function download(id,destination){const next=page.waitForEvent('download');await page.locator(id).click();await(await next).saveAs(destination);return readFile(destination);}
try{
  for(const version of (process.env.INPUT_VERSIONS||'baseline,optimized').split(',')){
    await page.goto(`${base}/${version}/dist/`);
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
    console.log(version,report.versions[version]);
  }
  await page.goto(`${base}/rhythm.html`);await page.locator('#example').click();await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);
  await page.locator('#analyze').click();await page.locator('#result').waitFor({state:'visible',timeout:90000});
  const event=JSON.parse(await download('#download',path.join(root,'tmp/browser/rhythm.event.json')));
  assert.equal(event.event_type,'rhythm_seed');assert.deepEqual(event.payload.rhythm.onset_beats,[0,1,1.5,3]);
  report.rhythm={bpm:event.payload.rhythm.tempo_bpm,onsets:event.payload.rhythm.onset_beats};
  assert.deepEqual(errors,[]);report.pageErrors=errors;
  await writeFile(path.join(root,'tmp/browser/report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
