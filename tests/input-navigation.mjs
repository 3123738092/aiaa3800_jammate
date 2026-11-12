import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage();
const base=process.env.INPUT_LAB_URL||'http://127.0.0.1:4185';
try {
  for (const route of ['/baseline/dist/','/optimized/dist/','/rhythm.html']) {
    await page.goto(base+route);
    await page.evaluate(()=>{
      window.stopped=0;
      navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>window.resolveMic=resolve);
      document.querySelector('#record').click();
    });
    await page.waitForFunction(()=>window.resolveMic);
    await page.evaluate(()=>{
      window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));
      window.resolveMic({getTracks:()=>[{stop:()=>window.stopped++}]});
    });
    await page.waitForFunction(()=>window.stopped===1);
    await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
    assert(await page.locator('#record').isEnabled(),route+' can record after return');
    await page.locator('#example').click();
    const action=route.includes('rhythm')?'#analyze':'#convert';
    await page.waitForFunction(id=>!document.querySelector(id).disabled,action);
    const source=await page.locator('#original').getAttribute('src');
    await page.evaluate(()=>{
      window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));
      window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
    });
    assert.equal(await page.locator('#original').getAttribute('src'),source);
    assert(await page.evaluate(async src=>(await fetch(src)).ok,source),route+' retains original on back/forward');
    console.log(route,'pending mic cleanup and cached-page restoration passed');
  }
} finally {await browser.close();}
