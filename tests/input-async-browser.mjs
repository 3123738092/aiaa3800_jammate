import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const base=process.env.INPUT_LAB_URL||'http://127.0.0.1:4186';
const browser=await chromium.launch({channel:'msedge',headless:true});const page=await browser.newPage();
try{
  await page.goto(base+'/rhythm.html');await page.click('#example');
  let release,arrived;const held=new Promise(r=>release=r),received=new Promise(r=>arrived=r);
  await page.route(base+'/api/rhythm/v1',async route=>{const response=await route.fetch();arrived();await held;await route.fulfill({response});},{times:1});
  await page.click('#analyze');await received;
  await page.evaluate(()=>{dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));});
  await page.locator('#file').setInputFiles({name:'invalid.wav',mimeType:'audio/wav',buffer:Buffer.from('bad audio')});
  await page.waitForFunction(()=>!document.querySelector('#file').disabled);
  const oldResponse=page.waitForResponse(base+'/api/rhythm/v1');release();await oldResponse;
  // Flush application continuation after response.json(), without a timing sleep.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#original').getAttribute('src'),null);
  assert(await page.locator('#band-panel').isHidden(),'old analysis must not restore a removed input');
  for(const route of ['/baseline/dist/','/optimized/dist/','/rhythm.html']){
    await page.goto(base+route);
    await page.evaluate(()=>{
      const decode=AudioContext.prototype.decodeAudioData;
      AudioContext.prototype.decodeAudioData=function(...args){
        AudioContext.prototype.decodeAudioData=decode;
        return new Promise((resolve,reject)=>window.releaseDecode=async()=>{try{resolve(await decode.apply(this,args));}catch(e){reject(e);}finally{window.decodeReleased=true;}});
      };
    });
    const source=fileURLToPath(new URL('../web/rhythm-example.wav',import.meta.url));
    await page.locator('#file').setInputFiles(source);await page.waitForFunction(()=>window.releaseDecode);
    await page.evaluate(()=>{dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));});
    assert.equal(await page.locator('#file').inputValue(),'',route+' can select the same file again after cancellation');
    await page.locator('#file').setInputFiles({name:'invalid.wav',mimeType:'audio/wav',buffer:Buffer.from('bad audio')});
    await page.waitForFunction(()=>!document.querySelector('#record').disabled);
    await page.evaluate(async()=>{await window.releaseDecode();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
    assert.equal(await page.locator('#original').getAttribute('src'),null,route+' old decode cannot restore removed audio');
  }
  console.log('Cancelled analysis and decoding cannot publish after page restoration/replacement.');
}finally{await browser.close();}
