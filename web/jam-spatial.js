// Visual-only PoC controller. Existing DOM controls remain the command/capture adapters.
const $=id=>document.getElementById(id),studio=document.querySelector('main.studio');
studio.classList.add('spatial-poc');
const objectHost=document.querySelector('.room-objects');
const mic=document.createElement('button');mic.type='button';mic.id='room-mic-model';mic.className='room-object room-microphone';mic.setAttribute('aria-label','聚焦麦克风，再次点击录音');
mic.innerHTML='<span class="microphone-render"><img src="assets/studio/jm-m1.png" alt="" class="microphone-static" width="960" height="840"><span id="microphone-canvas"></span></span><span class="object-name">麦克风<span id="microphone-caption">点按录音</span></span>';
document.querySelector('.room-band').replaceWith(mic);
const all=document.createElement('button');all.type='button';all.id='spatial-band-target';all.textContent='全乐队';all.setAttribute('aria-pressed','true');
const oldSelection=$('room-selection');oldSelection.hidden=true;
document.querySelector('.room-heading').replaceChildren(all,oldSelection);
const slides=[...objectHost.querySelectorAll('.room-object')];
objectHost.setAttribute('role','group');objectHost.setAttribute('aria-label','乐器浏览，可横向拖动或使用左右方向键');objectHost.tabIndex=0;
const nav=document.createElement('div');nav.className='spatial-nav';nav.innerHTML='<button type="button" aria-label="上一个乐器">‹</button><span id="spatial-object-name" aria-live="polite">麦克风</span><button type="button" aria-label="下一个乐器">›</button>';objectHost.after(nav);
const dots=document.createElement('span');dots.className='carousel-dots';dots.setAttribute('aria-hidden','true');dots.innerHTML='<i></i><i></i><i></i>';nav.insertBefore(dots,nav.lastElementChild);
let index=1,drag=null,suppressUntil=0;
function selectBand(){for(let i=0;i<3&&!$('studio-target').textContent.startsWith('全乐队');i++)$('studio-target').click();studio.dataset.target='all';}
function scope(i){if(i===1)selectBand();else document.querySelector(`.machine [data-target="${i===0?'drums':'keys'}"]`).click();}
function position(dragX=0){
 const step=Math.min(310,objectHost.clientWidth*.34);
 slides.forEach((item,i)=>{let offset=(i-index+3)%3;if(offset>1)offset-=3;const before=Number(item.dataset.offset);if(!dragX&&item.dataset.offset!==undefined&&Math.abs(offset-before)>1){item.classList.add('carousel-recycle');requestAnimationFrame(()=>requestAnimationFrame(()=>item.classList.remove('carousel-recycle')));}item.dataset.offset=offset;item.style.setProperty('--slide-x',`${offset*step+dragX}px`);item.style.setProperty('--slide-depth',`${Math.abs(offset)*-140}px`);item.style.setProperty('--slide-angle',`${offset*-12}deg`);item.dataset.focused=String(i===index);item.setAttribute('aria-current',String(i===index));});
 $('spatial-object-name').textContent=['鼓','麦克风','旋律'][index];
 [...dots.children].forEach((dot,i)=>dot.dataset.active=String(i===index));
 mic.removeAttribute('aria-pressed');
 all.setAttribute('aria-pressed',String(studio.dataset.target==='all'));
}
function focus(next,{apply=true}={}){index=(next+3)%3;if(apply)scope(index);position();}
slides.forEach((item,i)=>{item.onclick=null;item.addEventListener('click',event=>{if(performance.now()<suppressUntil){event.preventDefault();return;}if(index!==i){focus(i);return;}if(i===1)$('studio-mic').click();else{scope(i);if(i===2&&!$('room-keyboard').open)$('room-keyboard-open').click();}});});
all.onclick=()=>{selectBand();focus(1,{apply:false});};
nav.querySelectorAll('button').forEach((b,i)=>b.onclick=()=>focus(index+(i===0?-1:1)));
objectHost.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();focus(index+(e.key==='ArrowLeft'?-1:1));}else if(e.key==='Home'||e.key==='End'){e.preventDefault();focus(e.key==='Home'?0:2);}});
objectHost.addEventListener('pointerdown',e=>{if(e.button!==0)return;drag={x:e.clientX,y:e.clientY,id:e.pointerId,moved:false,vertical:false};});
objectHost.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(!drag.moved&&Math.abs(dy)>Math.abs(dx)&&Math.abs(dy)>8){drag.vertical=true;return;}if(drag.vertical)return;if(Math.abs(dx)>7){drag.moved=true;objectHost.setPointerCapture(e.pointerId);objectHost.classList.add('dragging');position(Math.max(-160,Math.min(160,dx))*.7);}});
function release(e){if(!drag||drag.id!==e.pointerId)return;const dx=e.clientX-drag.x,moved=drag.moved;drag=null;objectHost.classList.remove('dragging');if(moved){suppressUntil=performance.now()+350;focus(index+(dx<-45?1:dx>45?-1:0));}else position();}
objectHost.addEventListener('pointerup',release);objectHost.addEventListener('pointercancel',()=>{drag=null;objectHost.classList.remove('dragging');position();});
let wheelAt=0;objectHost.addEventListener('wheel',e=>{if(Math.abs(e.deltaX)<Math.abs(e.deltaY)||Math.abs(e.deltaX)<10)return;e.preventDefault();if(performance.now()-wheelAt<400)return;wheelAt=performance.now();focus(index+(e.deltaX>0?1:-1));},{passive:false});
new ResizeObserver(()=>position()).observe(objectHost);
new MutationObserver(()=>{const target=studio.dataset.target;focus(target==='drums'?0:target==='keys'?2:1,{apply:false});}).observe(studio,{attributes:true,attributeFilter:['data-target']});
position();
$('room-keyboard-open').textContent='键盘 / MIDI';
const midiPanel=document.createElement('div');midiPanel.className='midi-connections';
midiPanel.innerHTML='<button type="button" id="midi-connect">连接 MIDI 键盘</button><span id="midi-device-status" role="status">屏幕琴键可直接试听</span>';
$('room-keyboard').prepend(midiPanel);
let midiAccess=null;const midiInputs=new Set();
function bindMidi(){
 const connected=[...midiAccess.inputs.values()].filter(port=>port.state==='connected');
 $('midi-device-status').textContent=connected.length?connected.map(p=>p.name||'MIDI 键盘').join('、'):'未发现 MIDI 键盘 · 可使用屏幕琴键';
 for(const port of connected){if(midiInputs.has(port.id))continue;midiInputs.add(port.id);port.onmidimessage=event=>{const [command,note,velocity]=event.data;if((command&0xf0)===0x90&&velocity>0){window.dispatchEvent(new CustomEvent('jammate:midi-preview',{detail:{note,velocity}}));$('midi-device-status').textContent=`${port.name||'MIDI'} · 音符 ${note} · 力度 ${velocity}`;}};}
 for(const id of [...midiInputs])if(!connected.some(p=>p.id===id))midiInputs.delete(id);
}
$('midi-connect').onclick=async()=>{
 if(!navigator.requestMIDIAccess){$('midi-device-status').textContent='此浏览器不支持 MIDI · 屏幕琴键仍可试听';return;}
 try{midiAccess??=await navigator.requestMIDIAccess({sysex:false});midiAccess.onstatechange=bindMidi;bindMidi();$('midi-connect').textContent='刷新 MIDI 设备';}catch(error){$('midi-device-status').textContent=error.name==='NotAllowedError'?'未获得 MIDI 权限 · 可使用屏幕琴键':error.message;}
};

// Aligned readings retain backend-owned text nodes. Empty states have semantic labels.
const readout=document.querySelector('#room-readout .jam-readout');
const bar=$('jam-bar'),bpm=$('jam-current-bpm');
const barGroup=document.createElement('span');barGroup.className='transport-value';barGroup.append(bar,document.createTextNode('小节'));
const bpmGroup=document.createElement('span');bpmGroup.className='transport-value';bpmGroup.append(bpm,document.createTextNode('BPM'));
readout.replaceChildren($('jam-playback'),barGroup,bpmGroup);
function markEmpty(node){node.classList.toggle('value-empty',node.textContent.trim()==='—');}
[bar,bpm,...document.querySelectorAll('.jam-debug-music dd')].forEach(node=>{markEmpty(node);new MutationObserver(()=>markEmpty(node)).observe(node,{subtree:true,childList:true,characterData:true});});
const debugStats=document.querySelector('.jam-debug-music');
const summary=$('jam-debug-summary');summary.parentElement.replaceChildren(summary);
const updateSummary=()=>{summary.parentElement.hidden=summary.textContent==='等待开始';};
new MutationObserver(updateSummary).observe(summary,{childList:true,subtree:true,characterData:true});updateSummary();
document.querySelector('#room-settings header p')?.remove();
const empty=document.createElement('p');empty.className='inspector-empty';empty.textContent='开始演奏后显示速度、调性与和弦。';debugStats.after(empty);
const updateEmpty=()=>{const unset=[...debugStats.querySelectorAll('dd')].every(d=>d.textContent.trim()==='—');debugStats.hidden=unset;empty.hidden=!unset;};
new MutationObserver(updateEmpty).observe(debugStats,{subtree:true,childList:true,characterData:true});updateEmpty();

// A visual listbox fronts each real select; values, disabled flags and change events
// stay on the original element. Popovers remain inside their native parent dialog.
function refineSelect(select){
 if(select.dataset.refined)return;select.dataset.refined='true';
 const wrap=document.createElement('span');wrap.className='choice-field';select.before(wrap);wrap.append(select);select.classList.add('backing-select');select.tabIndex=-1;
 const trigger=document.createElement('button');trigger.type='button';trigger.className='choice-trigger';trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
 const pop=document.createElement('div');pop.className='choice-menu';pop.setAttribute('popover','auto');pop.setAttribute('role','listbox');pop.id='choice-'+select.id;
 const label=select.getAttribute('aria-label')||select.closest('label')?.textContent.split('\n')[0].trim()||document.querySelector(`label[for="${select.id}"]`)?.textContent||'选项';
 trigger.setAttribute('aria-label',label);trigger.setAttribute('aria-controls',pop.id);pop.setAttribute('aria-label',label);wrap.append(trigger);(select.closest('dialog')||studio).append(pop);
 function render(){
  trigger.textContent=select.selectedOptions[0]?.textContent||'选择';trigger.title=trigger.textContent;trigger.disabled=select.disabled;
  pop.replaceChildren(...[...select.options].map((option,i)=>{const b=document.createElement('button');b.type='button';b.setAttribute('role','option');b.setAttribute('aria-selected',String(option.selected));b.disabled=select.disabled||option.disabled||Boolean(option.parentElement.disabled);b.textContent=option.textContent;b.onclick=()=>{select.selectedIndex=i;select.dispatchEvent(new Event('change',{bubbles:true}));render();pop.hidePopover();trigger.focus();};return b;}));
 }
 function open(){
  if(trigger.disabled)return;render();
  const r=trigger.getBoundingClientRect(),dialog=select.closest('dialog'),bounds=dialog?.getBoundingClientRect();
  const minX=Math.max(12,(bounds?.left??0)+16),maxX=Math.min(innerWidth-12,(bounds?.right??innerWidth)-16);
  const minY=Math.max(12,(bounds?.top??0)+12),maxY=Math.min(innerHeight-12,(bounds?.bottom??innerHeight)-12);
  const textWidth=Math.max(...[...select.options].map(option=>[...option.textContent].reduce((sum,c)=>sum+(c.charCodeAt(0)>255?12:6.5),0)),100)+38;
  const width=Math.min(Math.max(r.width,textWidth),300,maxX-minX),height=Math.min(280,select.options.length*34+8,maxY-minY);
  const below=r.bottom+6,top=below+height<=maxY?below:Math.max(minY,r.top-height-6);
  pop.style.width=width+'px';pop.style.maxHeight=height+'px';pop.style.left=Math.max(minX,Math.min(r.left,maxX-width))+'px';pop.style.top=Math.min(Math.max(top,minY),maxY-height)+'px';
  pop.showPopover();trigger.setAttribute('aria-expanded','true');pop.querySelector('[aria-selected=true]:not(:disabled)')?.focus({preventScroll:true});
 }

 trigger.onclick=()=>{if(pop.matches(':popover-open'))pop.hidePopover();else open();};trigger.onkeydown=e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();open();}};
 pop.addEventListener('toggle',()=>trigger.setAttribute('aria-expanded',String(pop.matches(':popover-open'))));
 pop.addEventListener('keydown',e=>{const options=[...pop.querySelectorAll('button:not(:disabled)')],i=options.indexOf(document.activeElement);if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();options[e.key==='Home'?0:e.key==='End'?options.length-1:(i+(e.key==='ArrowDown'?1:-1)+options.length)%options.length]?.focus({preventScroll:true});}else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();pop.hidePopover();trigger.focus();}});
 select.addEventListener('change',render);new MutationObserver(render).observe(select,{childList:true,subtree:true,attributes:true,characterData:true});render();
 // Programmatic .value writes do not emit mutations. Refresh when its dialog opens.
 select.closest('dialog')?.addEventListener('toggle',render);select.closest('dialog')?.addEventListener('scroll',()=>{if(pop.matches(':popover-open'))pop.hidePopover();},{passive:true});
}
document.querySelectorAll('#room-settings select,#room-inspector select,#room-capture select').forEach(refineSelect);

// Rendering validation is deliberately an Inspector view, not a product workflow.
const inspector=$('room-inspector'),tabs=inspector.querySelector('.inspector-tabs');
const perfTab=document.createElement('button');perfTab.type='button';perfTab.setAttribute('role','tab');perfTab.setAttribute('aria-selected','false');perfTab.textContent='渲染验证';perfTab.id='room-tab-render';perfTab.setAttribute('aria-controls','render-validation');tabs.append(perfTab);
const perf=document.createElement('section');perf.id='render-validation';perf.hidden=true;perf.setAttribute('role','tabpanel');perf.setAttribute('aria-labelledby',perfTab.id);
perf.innerHTML=`<div class="render-toolbar"><div class="render-mode" role="group" aria-label="麦克风渲染方式"><button data-render-mode="light" aria-pressed="false">静态</button><button data-render-mode="3d" aria-pressed="false">3D</button></div><button id="render-benchmark">对比测试 · 各 8 秒</button></div><p id="render-status" role="status">准备局部渲染</p><p class="render-note">测试会持续重绘麦克风。播放中的排程重接次数来自真实音频会话；未播放时不验证音频。结果只适用于本机。</p><pre id="render-results">尚未测试</pre>`;
document.querySelector('.jam-debug-content').append(perf);
const allTabs=[...tabs.querySelectorAll('[role=tab]')];
function activate(tab){allTabs.forEach(t=>{const selected=t===tab;t.setAttribute('aria-selected',String(selected));t.tabIndex=selected?0:-1;const panel=$(t.getAttribute('aria-controls'));if(panel)panel.hidden=!selected;});}
allTabs.forEach((t,i)=>{t.onclick=()=>activate(t);t.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const j=e.key==='Home'?0:e.key==='End'?allTabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+allTabs.length)%allTabs.length;activate(allTabs[j]);allTabs[j].focus();}};});
perfTab.tabIndex=-1;
const low=navigator.connection?.saveData||(navigator.deviceMemory&&navigator.deviceMemory<=4)||(navigator.hardwareConcurrency&&navigator.hardwareConcurrency<=4);
let renderMode=new URLSearchParams(location.search).get('render')==='3d'&&!low?'3d':'light',scene=null,loading=null,recording=false,latestAudio=null,audioContext=null;
const timings={},renderResources=new Map();
function collectRenderResource(r){if(/jm-m1|studio_small_09|microphone-scene|fonts\//.test(r.name))renderResources.set(r.name,{file:r.name.split('/').pop(),durationMs:Math.round(r.duration),transferBytes:r.transferSize,decodedBytes:r.decodedBodySize});}
performance.getEntriesByType('resource').forEach(collectRenderResource);
try{new PerformanceObserver(list=>list.getEntries().forEach(collectRenderResource)).observe({type:'resource',buffered:true});}catch{}

window.addEventListener('jammate:audio',({detail})=>{audioContext=detail.context;});
window.addEventListener('jammate:playback-metrics',({detail})=>{latestAudio=detail;});
async function loadScene(){
 if(scene)return scene;if(loading)return loading;
 loading=(async()=>{const t=performance.now();const {createMicrophoneScene}=await import('./vendor/microphone-scene.js');timings.moduleMs=performance.now()-t;scene=await createMicrophoneScene($('microphone-canvas'),{onFailure:message=>{renderMode='light';setMode('light');$('render-status').textContent=message;}});Object.assign(timings,scene.timing);timings.totalMs=performance.now()-t;scene.record(recording);return scene;})();
 try{return await loading;}catch(error){loading=null;throw error;}
}
async function setMode(mode){
 renderMode=mode;mic.dataset.renderMode=mode;perf.querySelectorAll('[data-render-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.renderMode===mode)));
 if(mode==='light'){scene?.setActive(false);$('render-status').textContent='静态图 · 不创建渲染循环';return;}
 $('render-status').textContent='正在加载局部 3D';
 try{const s=await loadScene();if(renderMode!=='3d'){s.setActive(false);return;}s.setActive(true);mic.dataset.ready='true';$('render-status').textContent=`3D 就绪 · ${Math.round(timings.totalMs)} ms · ${s.stats().calls} 次绘制`;}
 catch(error){mic.dataset.renderMode='light';renderMode='light';$('render-status').textContent='3D 不可用，已使用静态图：'+error.message;}
}
perf.querySelectorAll('[data-render-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.renderMode));
mic.addEventListener('pointermove',e=>{if(drag?.moved)return;const r=mic.getBoundingClientRect();scene?.pose((e.clientX-r.left)/r.width-.5,(e.clientY-r.top)/r.height-.5);});mic.addEventListener('pointerleave',()=>scene?.pose());
function reflectRecording(){
 const manual=[...document.querySelectorAll('[data-record]')].some(button=>button.textContent.includes('停止'));
 const opening=$('opening-mic').getAttribute('aria-pressed')==='true' && $('opening-mic').textContent.includes('结束');
 const voice=$('jam-microphone').getAttribute('aria-pressed')==='true' && /^(正在监听|正在听|正在识别|已识别|识别超时)/.test($('jam-voice-status').textContent);
 const nextRecording=manual||opening||voice;if(nextRecording===recording)return;recording=nextRecording;
 mic.classList.toggle('is-recording',recording);mic.setAttribute('aria-label',recording?'正在录音，点击结束':'聚焦麦克风，再次点击录音');$('microphone-caption').textContent=recording?'正在录音 · 点按结束':'点按录音';scene?.record(recording);
}
const recordingObserver=new MutationObserver(reflectRecording);
['opening-mic','jam-microphone','jam-voice-status'].forEach(id=>recordingObserver.observe($(id),{attributes:true,childList:true,subtree:true,characterData:true}));
document.querySelectorAll('[data-record]').forEach(button=>recordingObserver.observe(button,{childList:true,subtree:true,characterData:true}));

const longTasks=[];let longTaskSupported=false;try{new PerformanceObserver(list=>longTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});longTaskSupported=true;}catch{}
async function sample(mode){
 if(document.hidden)throw new Error('验证页进入后台，请保持在前台再测试。');
 await setMode(mode);if(document.hidden)throw new Error('验证页进入后台，请保持在前台再测试。');
 const start=performance.now(),audioStart=audioContext?.currentTime,initial=latestAudio?{...latestAudio}:null,initialGpuFrames=scene?.stats().renderedFrames||0,intervals=[];let last=start,notRunning=0;
 try{
  await new Promise((resolve,reject)=>{
   let frame=0,guard=0,finished=false;
   const finish=error=>{if(finished)return;finished=true;cancelAnimationFrame(frame);clearTimeout(guard);document.removeEventListener('visibilitychange',visibility);error?reject(error):resolve();};
   const visibility=()=>{if(document.hidden)finish(new Error('验证页进入后台，本次性能对比已停止。'));};
   const step=t=>{if(finished)return;intervals.push(t-last);last=t;if(audioContext&&audioContext.state!=='running')notRunning++;if(mode==='3d')scene?.stress(t);else mic.querySelector('img').style.transform=`rotateY(${Math.sin(t*.001)*8}deg)`;if(t-start>=8000)finish();else frame=requestAnimationFrame(step);};
   document.addEventListener('visibilitychange',visibility);guard=setTimeout(()=>finish(new Error('帧回调被节流或停止，本次性能对比无效。')),12000);frame=requestAnimationFrame(step);
  });
 }finally{mic.querySelector('img').style.transform='';scene?.pose();}
 const elapsed=performance.now()-start,sorted=[...intervals].sort((a,b)=>a-b),final=latestAudio;
 const audioVerified=Boolean(initial&&final&&initial.session===final.session&&audioContext?.state==='running'&&audioContext.currentTime-audioStart>elapsed/1000*.85);
 return {mode,durationMs:Math.round(elapsed),rafFps:+(intervals.length/elapsed*1000).toFixed(1),p95FrameMs:sorted.length?+sorted[Math.floor(sorted.length*.95)].toFixed(1):null,framesOver50ms:intervals.filter(t=>t>50).length,longTasks:longTaskSupported?longTasks.filter(t=>t.start>=start&&t.start<start+elapsed).length:null,
 audio:audioVerified?{session:final.session,contextState:audioContext.state,audioClockSeconds:+(audioContext.currentTime-audioStart).toFixed(2),gapDelta:final.gaps-initial.gaps,failures:final.failures,nonRunningFrameSamples:notRunning,barStart:initial.bar,barEnd:final.bar}:{verified:false,reason:'无持续播放会话，或会话在测试中改变'},...(mode==='3d'?{gpu:{...scene.stats(),sampleRenderedFrames:scene.stats().renderedFrames-initialGpuFrames}}:{})};
}

$('render-benchmark').onclick=async()=>{
 const controls=[...perf.querySelectorAll('button')];controls.forEach(b=>b.disabled=true);const before=renderMode;
 try{await loadScene();$('render-results').textContent='正在测试静态方案（8 秒）…';const light=await sample('light');$('render-results').textContent='静态完成，正在测试 3D（8 秒）…';const three=await sample('3d');const resources=[...renderResources.values()];const comparisonValid=light.rafFps>=10&&three.gpu?.sampleRenderedFrames>0;$('render-results').textContent=JSON.stringify({measuredAt:new Date().toISOString(),comparisonValid,warning:comparisonValid?null:'静态基线吞吐过低或没有实际 3D 绘制。本次仅记录观测，不用于评价渲染成本。',viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},hardware:{cores:navigator.hardwareConcurrency,memoryGB:navigator.deviceMemory||null},loading:timings,resources,method:'同一页面预热后，静态变换与连续 3D 重绘各 8 秒；rAF 非 GPU 帧率；音频仅验证调度计数与时钟，不代表听感检测。',samples:[light,three]},null,2);}
 catch(error){$('render-results').textContent='测试未完成：'+error.message;}
 finally{await setMode(before);controls.forEach(b=>b.disabled=false);}
};
// Static first paint; no WebGL download on constrained devices or explicit light mode.
if(renderMode==='light')setMode('light');else (window.requestIdleCallback||((fn)=>setTimeout(fn,200)))(()=>setMode('3d'),{timeout:1200});
window.addEventListener('pagehide',()=>scene?.dispose(),{once:true});
