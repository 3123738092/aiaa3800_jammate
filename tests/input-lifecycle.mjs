import {createRequire}from'node:module';import{readFile,writeFile,mkdir}from'node:fs/promises';import path from'node:path';import assert from'node:assert/strict';
const require=createRequire(import.meta.url);const{chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const root=path.resolve(import.meta.dirname,'..'),base=process.env.INPUT_LAB_URL||'http://127.0.0.1:4175';
const browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(`${base}/rhythm.html`);
 await page.evaluate(()=>{window.micRequests=0;window.micRejectors=[];navigator.mediaDevices.getUserMedia=()=>{window.micRequests++;return new Promise((resolve,reject)=>window.micRejectors.push(reject));};document.getElementById('record').click();document.getElementById('record').click();});
 assert.equal(await page.evaluate(()=>window.micRequests),1,'one microphone request while permission is pending');
 for(const id of ['example','file','analyze'])assert(await page.locator(`#${id}`).isDisabled(),id+' locked');
 await page.evaluate(()=>window.micRejectors.forEach(reject=>reject(new DOMException('Permission denied','NotAllowedError'))));
 await page.waitForFunction(()=>!document.getElementById('record').disabled);assert.match(await page.locator('#status').innerText(),/权限/);
 await page.evaluate(async()=>{
   const blob=await(await fetch('rhythm-example.wav')).blob();window.stoppedTracks=0;
   navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop:()=>window.stoppedTracks++}]});
   window.MediaRecorder=class{state='inactive';mimeType='audio/wav';start(){this.state='recording';}stop(){this.state='inactive';queueMicrotask(()=>{this.ondataavailable({data:blob});this.onstop();});}};
 });
 await page.locator('#record').click();await page.waitForFunction(()=>document.getElementById('record').textContent==='停止录音');
 assert(await page.locator('#file').isDisabled());assert(await page.locator('#example').isDisabled());
 await page.locator('#record').click();await page.waitForFunction(()=>!document.getElementById('analyze').disabled);
 assert.equal(await page.evaluate(()=>window.stoppedTracks),1,'recording releases microphone track');
 await page.locator('#example').click();await page.waitForFunction(()=>!document.getElementById('analyze').disabled);
 assert(await page.locator('#original').getAttribute('src'));
 await page.locator('#file').setInputFiles({name:'broken.wav',mimeType:'audio/wav',buffer:Buffer.from('not audio')});
 await page.waitForFunction(()=>!document.getElementById('file').disabled);
 assert(await page.locator('#analyze').isDisabled(),'broken input cannot analyze stale buffer');
 assert.equal(await page.locator('#original').getAttribute('src'),null,'stale original removed');assert(await page.locator('#result').isHidden());
 await page.locator('#example').click();await page.waitForFunction(()=>!document.getElementById('analyze').disabled);
 await page.locator('#analyze').click();await page.locator('#result').waitFor({state:'visible',timeout:90000});
 await page.locator('summary').click();await page.locator('#tempo').fill('110');await page.locator('#tempo').blur();assert(await page.locator('#result').isHidden(),'changed settings invalidate old event');
 await page.locator('#analyze').click();await page.locator('#result').waitFor({state:'visible',timeout:90000});
 await page.screenshot({path:path.join(root,'tmp/browser/rhythm.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'rhythm mobile overflow');
 const invalid=await page.request.post(`${base}/api/validate`,{data:{source:'rhythm'}});assert.equal(invalid.status(),400);
 assert.deepEqual(errors,[]);await writeFile(path.join(root,'docs/integration/round-4-browser.json'),JSON.stringify({permissionPendingLocked:true,permissionDenialRecovered:true,mockedRecordingReleasesTrack:true,brokenFileClearsOldAudio:true,settingsInvalidateResult:true,recoveryAnalysisPassed:true,mobileOverflow:false,pageErrors:errors},null,2)+'\n');
 console.log('Lifecycle regression passed; no physical microphone was used.');
}finally{await browser.close();}
