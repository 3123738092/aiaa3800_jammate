// Room presentation. All capture, configuration and trace nodes keep their real handlers.
const $ = id => document.getElementById(id);
const studio = document.querySelector('main.studio');
studio.classList.add('listening-room');
document.querySelector('.brand-mark').remove();
document.querySelector('.brand-period').remove();
document.querySelector('.studio-intro').remove();
document.querySelector('.studio-footer').remove();
$('studio-options').textContent = '设置';
$('studio-inspect').textContent = '运行日志';
$('studio-save').textContent = '保存录音';
document.querySelectorAll('.target-select, #studio-rhythm, #studio-hum').forEach(el => { el.textContent = el.textContent.replace(/↗/g, '').trim(); });
const room = document.createElement('section');
room.className = 'room-scene'; room.setAttribute('aria-label','点击乐器，选择你想交流的对象');
room.innerHTML = `
 <div class="room-heading"><span id="room-selection">全乐队</span><span>点一下乐器，再说说你的想法</span></div>
 <div class="room-objects">
  <button class="room-object room-drums target-select" data-target="drums" aria-pressed="false" aria-label="选择鼓组"><span class="object-art drum-art" aria-hidden="true"><span class="drum-shell"></span><span class="drum-head"></span><i></i><i></i><i></i><span class="drum-stick"></span></span><span class="object-name">鼓<span>给节奏提个要求</span></span></button>
  <button class="room-object room-band target-select" data-target="all" aria-pressed="true" aria-label="选择全乐队"><span class="object-art turntable-art" aria-hidden="true"><span class="deck-plinth"></span><span class="record"><span class="record-label">JamMate<span>SIDE A</span></span></span><span class="tone-arm"></span><span class="record-led"></span></span><span class="object-name">全乐队<span>一起换个感觉</span></span></button>
  <button class="room-object room-melody target-select" data-target="keys" aria-pressed="false" aria-label="选择旋律组"><span class="object-art mini-keyboard" aria-hidden="true"><span class="mini-controls"><i></i><i></i><b></b><i></i></span><span class="mini-keys">${Array.from({length:10},(_,i)=>`<i${[0,1,3,4,5,7,8].includes(i)?' class="with-black"':''}></i>`).join('')}</span></span><span class="object-name">旋律<span>哼一句给它听</span></span></button>
 </div>
 <div class="room-playback"><div id="room-transport"></div><div id="room-readout"></div><button id="room-keyboard-open" aria-expanded="false" aria-controls="room-keyboard">演奏键盘</button></div>`;
const machine = document.querySelector('.machine');
machine.before(room);
const keyboard = document.createElement('details'); keyboard.id='room-keyboard';
keyboard.innerHTML='<summary>演奏键盘 <span>本地试听</span></summary>';
room.after(keyboard); keyboard.append(machine);
$('room-transport').append(document.querySelector('.jam-buttons'));
$('room-readout').append(document.querySelector('.jam-readout'));
$('room-keyboard-open').onclick=()=>{keyboard.open=!keyboard.open; if(keyboard.open) keyboard.scrollIntoView({block:'nearest',behavior:'smooth'});};
keyboard.addEventListener('toggle',()=>$('room-keyboard-open').setAttribute('aria-expanded',String(keyboard.open)));
const command = $('studio-command');
let submittedDraft = '';
command.addEventListener('submit', () => { submittedDraft = $('studio-words').value; });
window.addEventListener('jammate:submitted', ({detail}) => {
 if(detail.source==='text' && $('studio-words').value===submittedDraft)$('studio-words').value='';
});
const actions = document.createElement('div'); actions.className='room-input-actions';
actions.innerHTML=`<div class="room-input-modes" role="group" aria-label="输入方式"><button type="button" data-input-mode="speech" aria-pressed="true">说句话</button><button type="button" data-input-mode="humming" aria-pressed="false">哼一段</button><button type="button" data-input-mode="rhythm" aria-pressed="false">拍节奏</button></div><span id="room-input-hint">点击麦克风开始，再点一次结束</span>`;
command.before(actions);
$('studio-send').textContent='发送';
const mic = $('studio-mic');
mic.innerHTML='<span class="room-mic-grille" aria-hidden="true"></span>';
const micLabel = document.createElement('span');micLabel.id='room-mic-label';micLabel.textContent='录音';mic.append(micLabel);
const legacy = $('studio-legacy');
legacy.hidden=true;
const settings = document.createElement('dialog');settings.id='room-settings';settings.className='room-dialog';
settings.innerHTML=`<header><div><h2>设置</h2><p>按需要调整，关闭后继续演奏。</p></div><button type="button" data-close aria-label="关闭设置">关闭</button></header><div class="room-settings-body"><section id="room-session-settings"><h3>演奏方式</h3></section><section id="room-sound-settings"><h3>声音与编排</h3></section><section id="room-voice-settings"><h3>输入与确认</h3></section></div>`;
studio.append(settings);
const move=(node,dest)=>{if(node)$(dest).append(node);};
move(document.querySelector('.jam-opening-mode'),'room-session-settings');
move($('jam-cold-settings'),'room-session-settings');
move($('jam-warm-bpm'),'room-session-settings');
move($('jam-agent').closest('label'),'room-sound-settings');
const advanced=document.querySelector('.jam-advanced');
[...advanced.children].filter(n=>n.tagName!=='SUMMARY').forEach(n=>move(n,n.contains($('jam-input-mode'))?'room-voice-settings':'room-sound-settings'));
move($('jam-mrt2-auto-row'),'room-sound-settings');move($('jam-mrt2-hint'),'room-sound-settings');
const voiceOptions=document.createElement('div');voiceOptions.className='room-voice-options';
voiceOptions.append($('jam-captions-toggle'),$('jam-nod-toggle'));$('room-voice-settings').append(voiceOptions);
$('jam-opening-mode').options[0].textContent='让乐队补全';
$('jam-opening-mode').options[1].textContent='从我的素材开始';
const drawer=$('studio-drawer');drawer.hidden=true;
$('studio-options').onclick=()=>settings.showModal();
settings.querySelector('[data-close]').onclick=()=>settings.close();
settings.addEventListener('close',()=>$('studio-options').setAttribute('aria-expanded','false'));
settings.addEventListener('toggle',()=>$('studio-options').setAttribute('aria-expanded',String(settings.open)));
// Keep both capture cards within #jam-opening, as required by the existing input controller.
const capture=$('jam-opening');
const preserved=[...capture.children];
const captureRegion=document.createElement('div');captureRegion.id='room-capture';captureRegion.hidden=true;
captureRegion.innerHTML='<div class="capture-heading"><h2 id="room-capture-title">哼一段旋律</h2><button type="button" id="room-capture-close">收起</button></div>';
const cards=document.querySelector('.jam-opening-inputs');
capture.replaceChildren();
capture.append(cards,preserved.find(n=>n.id==='jam-opening-status'),preserved.find(n=>n.id==='jam-opening-result'));
const serviceInputs=document.createElement('div');serviceInputs.hidden=true;
preserved.filter(node=>node!==cards && !capture.contains(node)).forEach(node=>serviceInputs.append(node));
capture.append(serviceInputs);captureRegion.append(capture);
document.querySelector('.studio-feedback').after(captureRegion);
document.querySelector('[data-jam-input="humming"] h3').textContent='把你的旋律交给乐队';
document.querySelector('[data-jam-input="rhythm"] h3').textContent='拍出你想要的节奏';
const panels=[...document.querySelectorAll('[data-jam-input]')];
let mode='speech';
function chooseMode(next) {
 const recording=panels.some(p=>p.querySelector('[data-record]').textContent.includes('停止'));
 if(recording && mode!==next){$('studio-message').textContent='请先结束当前录音，再切换输入方式。';return;}
 mode=next;
 if(mode==='humming')document.querySelector('.machine [data-target="keys"]').click();
 if(mode==='rhythm')document.querySelector('.machine [data-target="drums"]').click();
 actions.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.inputMode===mode)));
 captureRegion.hidden=mode==='speech';
 panels.forEach(p=>p.hidden=p.dataset.jamInput!==mode);
 $('room-capture-title').textContent=mode==='rhythm'?'拍一小段节奏':'哼一段旋律';
 $('room-input-hint').textContent=mode==='speech'?'点击麦克风开始，再点一次结束':mode==='rhythm'?'跟着自己的感觉拍一小节，也可以上传录音':'不用会弹琴，哼出脑海里的旋律就好';
 mic.setAttribute('aria-label',mode==='speech'?'语音指令录音':mode==='rhythm'?'拍手节奏录音':'哼唱旋律录音');
}
actions.querySelectorAll('button').forEach(b=>b.onclick=()=>chooseMode(b.dataset.inputMode));
$('room-capture-close').onclick=()=>chooseMode('speech');
document.querySelectorAll('.room-object').forEach(button=>button.onclick=()=>{
 document.querySelector(`.machine [data-target="${button.dataset.target}"]`)?.click();
 if(button.dataset.target==='all'){while(!$('studio-target').textContent.startsWith('全乐队'))$('studio-target').click();}
 updateSelection(button.dataset.target);
});
function updateSelection(target) {
 studio.dataset.target=target;
 document.querySelectorAll('.room-object').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.target===target)));
 $('room-selection').textContent=({all:'全乐队',drums:'鼓',keys:'旋律'})[target]||'全乐队';
 $('studio-words').placeholder=({all:'想听什么，或想让音乐怎么变？',drums:'告诉鼓手：轻一点、密一点…',keys:'告诉键盘手：简单一点、柔和一点…'})[target];
}
new MutationObserver(()=>{
 const text=$('studio-target').textContent;
 updateSelection(text.startsWith('节奏组')?'drums':text.startsWith('旋律组')?'keys':'all');
}).observe($('studio-target'),{childList:true,subtree:true});
updateSelection('all');
$('studio-rhythm').onclick=()=>chooseMode('rhythm');
$('studio-hum').onclick=()=>chooseMode('humming');
const speechMic=mic.onclick;
mic.onclick=()=>{ if(mode==='speech')speechMic();else { const button=document.querySelector(`[data-jam-input="${mode}"] [data-record]`);if(!button.disabled)button.click(); }};
const captureObserver=new MutationObserver(()=>{
 const active=panels.find(p=>p.querySelector('[data-record]').textContent.includes('停止'));
 if(active && mode!==active.dataset.jamInput)chooseMode(active.dataset.jamInput);
 const recording=Boolean(active)||$('opening-mic').getAttribute('aria-pressed')==='true'||$('jam-microphone').getAttribute('aria-pressed')==='true';
 mic.setAttribute('aria-pressed',String(recording));$('room-mic-label').textContent=recording?'结束':'录音';
});
panels.forEach(p=>captureObserver.observe(p,{attributes:true,subtree:true,childList:true,characterData:true}));
['opening-mic','jam-microphone'].forEach(id=>captureObserver.observe($(id),{attributes:true}));
['jam-captions','jam-transcript','jam-nod'].forEach(id=>move($(id),'room-capture'));
// Transcripts and confirmation remain visible even when the capture panel is collapsed.
const review=document.querySelector('.studio-review');
['jam-captions','jam-transcript','jam-nod'].forEach(id=>review.append($(id)));
// Structured Inspector: the real trace controller continues to populate the same nodes.
const inspector=document.createElement('dialog');inspector.id='room-inspector';inspector.className='room-dialog room-inspector';
inspector.innerHTML=`<header><div><h2>运行日志</h2><p>本次会话的真实输入、决策与音频输出</p></div><button type="button" data-close aria-label="关闭运行日志">关闭</button></header><nav class="inspector-tabs" aria-label="日志视图" role="tablist"><button role="tab" aria-selected="true" data-view="trace" aria-controls="room-trace">处理过程</button><button role="tab" aria-selected="false" data-view="music" aria-controls="room-music">音乐状态</button><button role="tab" aria-selected="false" data-view="raw" aria-controls="room-raw">原始数据</button></nav>`;
studio.append(inspector);
const debug=$('jam-debug');inspector.append(debug);
debug.querySelector('summary').onclick=e=>e.preventDefault();
const content=debug.querySelector('.jam-debug-content');
const trace=document.createElement('section');trace.id='room-trace';trace.setAttribute('role','tabpanel');
const music=document.createElement('section');music.id='room-music';music.hidden=true;music.setAttribute('role','tabpanel');
const raw=document.createElement('section');raw.id='room-raw';raw.hidden=true;raw.setAttribute('role','tabpanel');
const children=[...content.children];let current=trace,toolbarCount=0;
for(const child of children){
 if(child.matches('.jam-debug-music')){content.prepend(child);continue;}
 if(child.matches('.jam-debug-toolbar')&&++toolbarCount===2)current=music;
 if(child.matches('.jam-debug-data'))current=raw;
 current.append(child);
}
content.append(trace,music,raw);
inspector.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{
 inspector.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',String(b===button)));
 [trace,music,raw].forEach(panel=>panel.hidden=panel.id!=='room-'+button.dataset.view);
 inspector.querySelectorAll('[data-view]').forEach(b=>b.tabIndex=b===button?0:-1);
});
const tabs=[...inspector.querySelectorAll('[data-view]')];
tabs.forEach((button,index)=>{button.tabIndex=index===0?0:-1;button.id='room-tab-'+button.dataset.view;$('room-'+button.dataset.view).setAttribute('aria-labelledby',button.id);button.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();};});
$('studio-inspect').onclick=()=>{debug.open=true;inspector.showModal();};
inspector.querySelector('[data-close]').onclick=()=>inspector.close();
inspector.addEventListener('close',()=>{debug.open=false;});
for(const dialog of [settings,inspector])dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
const preview=$('jam-preview-output');studio.append(preview);
