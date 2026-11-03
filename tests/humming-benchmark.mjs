import {createRequire}from'node:module';import{readFile,writeFile,mkdir}from'node:fs/promises';import path from'node:path';import assert from'node:assert/strict';
const require=createRequire(import.meta.url);const{chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const root=path.resolve(import.meta.dirname,'..');await mkdir(path.join(root,'tmp/benchmark'),{recursive:true});
const expected=JSON.parse(await readFile(path.join(root,'web/fixtures/expected.json')));
const browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage({acceptDownloads:true});const report={type:'synthetic harmonic audio; not human accuracy',cases:{}};
try{
for(const version of (process.env.HUM_BENCHMARK_VERSIONS||'baseline,optimized').split(',')){
 await page.goto(`${process.env.INPUT_LAB_URL||'http://127.0.0.1:4185'}/${version}/dist/`);
 await page.waitForFunction(()=>document.querySelector('#model-badge.ready'),null,{timeout:90000});
 for(const[name,golden]of Object.entries(expected)){
  await page.locator('#file').setInputFiles(path.join(root,`web/fixtures/${name}.wav`));await page.waitForFunction(()=>!document.querySelector('#convert').disabled);
  await page.locator('#convert').click();await page.locator('#result-content').waitFor({state:'visible',timeout:120000});
  const pending=page.waitForEvent('download');await page.locator('#download-json').click();const file=path.join(root,`tmp/benchmark/${version}-${name}.json`);await(await pending).saveAs(file);
  const doc=JSON.parse(await readFile(file));const notes=doc.notes.map(n=>({pitch:n.pitchMidi,start:n.startSec,duration:n.durationSec}));
  const entry={expected:golden.notes,actual:notes,rawCount:doc.results.raw.notes.length,count:notes.length,elapsedMs:doc.analysisElapsedMs};
  entry.pitchSequenceCorrect=JSON.stringify(notes.map(n=>n.pitch))===JSON.stringify(golden.notes.map(n=>n.pitch));
  entry.maxBoundaryErrorSec=entry.pitchSequenceCorrect?Math.max(...notes.flatMap((n,i)=>[Math.abs(n.start-golden.notes[i].start),Math.abs(n.start+n.duration-golden.notes[i].start-golden.notes[i].duration)])):null;
  report.cases[`${version}-${name}`]=entry;console.log(version,name,entry.count,entry.pitchSequenceCorrect,entry.maxBoundaryErrorSec);
 }
}
await writeFile(path.join(root,process.env.HUM_BENCHMARK_REPORT||'docs/integration/humming-benchmark.json'),JSON.stringify(report,null,2)+'\n');
// Sustained-tone and rest preservation are the core acceptance gates. Short
// notes remain an explicit exploratory case, not a silently relaxed assertion.
for(const name of ['sustain','repeated','steps','rest','short','quiet','noisy']){const e=report.cases[`optimized-${name}`];assert(e.pitchSequenceCorrect,name);assert(e.maxBoundaryErrorSec<.13,name+' timing');}
}finally{await browser.close();}
