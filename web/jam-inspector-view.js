// Inspection views read the existing, backend-populated DOM. No synthetic events,
// inferred completion states, extra backend requests or changes to capture/transport.
const $=id=>document.getElementById(id);
const mic=$('studio-mic');
mic.innerHTML='<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2.5" width="6" height="12" rx="3"/><path d="M5.5 10.5v2a6.5 6.5 0 0 0 13 0v-2M12 19v3m-4 0h8"/></svg><span id="room-mic-label" class="accessible-label">录音</span>';
mic.title='点击开始录音，再点一次结束';
const inputActions=document.querySelector('.room-input-actions');
inputActions.append($('studio-target'));
$('room-input-hint').hidden=true;
// Existing click behavior is retained; the keyboard can also open directly from
// the already-focused melody object through its existing controls.

const inspector=$('room-inspector');
const content=document.querySelector('.jam-debug-content');
const stats=document.querySelector('.jam-debug-music'),statsEmpty=stats.nextElementSibling;
$('room-music').prepend(stats,statsEmpty);
const trace=$('room-trace'),pipeline=$('jam-debug-pipeline');
const sourceControls=trace.querySelector('.jam-debug-toolbar>div');
const context=document.createElement('div');context.className='inspector-context';
context.append(sourceControls,$('jam-debug-turn-status'));
trace.before(context);
trace.querySelector('.jam-debug-toolbar').remove();
$('jam-debug-status').classList.add('inspector-status');
const status=$('jam-debug-status');status.title='保留最近 12 轮。小节显示从 1 起，原始 JSON 从 0 起。';
const updateStatus=()=>{status.hidden=/^(保留最近|开始演奏后记录)/.test(status.textContent);};
new MutationObserver(updateStatus).observe(status,{childList:true,subtree:true,characterData:true});updateStatus();
$('jam-debug-copy').textContent='复制本次数据';
const explorer=document.createElement('div');explorer.className='trace-explorer';
explorer.innerHTML='<nav class="stage-rail" role="tablist" aria-orientation="vertical" aria-label="处理阶段"></nav><section class="stage-inspection"><header class="stage-heading"><h3 id="stage-title">输入</h3><div class="stage-view-tabs" role="group" aria-label="阶段数据视图"><button data-view="summary" aria-pressed="true">摘要</button><button data-view="diff" aria-pressed="false" hidden>状态变化</button><button data-view="json" aria-pressed="false">JSON</button></div></header><div id="stage-summary"></div><div id="stage-diff" hidden></div><div id="stage-json" hidden><p id="stage-json-caption"></p><pre id="stage-json-code"></pre><p id="stage-json-empty">尚未收到该阶段数据</p></div></section>';
trace.append(explorer);$('stage-summary').append(pipeline);
const stages=[
 {title:'输入',field:'jam-debug-event'},
 {title:'InputEvent',field:'jam-debug-event'},
 {title:'JamState',field:'jam-debug-state'},
 {title:'BandPlan',field:'jam-debug-plan'},
 {title:'Renderer',field:'jam-debug-models'},
 {title:'播放回执',field:'jam-debug-plan'},
];
const stagePanel=explorer.querySelector('.stage-inspection');stagePanel.id='stage-inspection';stagePanel.setAttribute('role','tabpanel');
let selectedStage=0,view='summary';
const rail=explorer.querySelector('.stage-rail');
const stageButtons=stages.map((stage,index)=>{
 const button=document.createElement('button');button.type='button';button.setAttribute('role','tab');button.id='stage-tab-'+index;button.setAttribute('aria-controls','stage-inspection');
 button.innerHTML='<i aria-hidden="true"></i><span>'+stage.title+'</span><small>未收到</small>';
 button.onclick=()=>{selectedStage=index;if(view==='diff'&&index!==2)view='summary';paintStage();};
 button.onkeydown=e=>{if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?5:(index+(['ArrowDown','ArrowRight'].includes(e.key)?1:-1)+6)%6;stageButtons[next].click();stageButtons[next].focus({preventScroll:true});};rail.append(button);return button;
});
explorer.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{view=button.dataset.view;paintStage();});
function hasPayload(pre){return pre && !['','—','null'].includes(pre.textContent.trim());}
function flatten(value,path='',out={}){
 if(value && typeof value==='object'){
  const entries=Object.entries(value);if(!entries.length)out[path]=Array.isArray(value)?'[]':'{}';
  for(const [key,item]of entries)flatten(item,path?(Array.isArray(value)?`${path}[${key}]`:`${path}.${key}`):key,out);
 }else out[path]=JSON.stringify(value);
 return out;
}
function paintDiff(){
 const holder=$('stage-diff');let state;
 try{state=JSON.parse($('jam-debug-state').textContent);}catch{}
 if(!state?.at_decision || !state?.after_playback){holder.innerHTML='<p class="inspection-empty">等待同一次输入的决策状态与播放后状态。</p>';return;}
 const before=flatten(state.at_decision),after=flatten(state.after_playback),changes=[...new Set([...Object.keys(before),...Object.keys(after)])].filter(key=>before[key]!==after[key]).sort((a,b)=>{const rank=key=>key.startsWith('arrangement.')?0:key.startsWith('harmony.')?1:key.startsWith('form.')?2:key.startsWith('transport.')?3:4;return rank(a)-rank(b);});
 holder.replaceChildren();
 const intro=document.createElement('p');intro.className='diff-intro';intro.textContent=changes.length?`${changes.length} 处变化 · 决策时 → 播放后`:'两个状态之间没有字段变化';intro.title='差异包含正常播放推进，不表示每个字段变化都由该指令直接造成。';holder.append(intro);
 if(!changes.length)return;
 const table=document.createElement('table');table.innerHTML='<thead><tr><th>字段</th><th>决策时</th><th>播放后</th></tr></thead><tbody></tbody>';
 for(const key of changes.slice(0,100)){
  const row=document.createElement('tr');for(const text of [key,before[key]??'未提供',after[key]??'未提供']){const cell=document.createElement('td');cell.textContent=text;row.append(cell);}table.querySelector('tbody').append(row);
 }holder.append(table);
 if(changes.length>100){const note=document.createElement('p');note.textContent='显示前 100 项，完整数据见 JSON。';holder.append(note);}
}
function paintStage(){
 const rows=[...pipeline.children];
 stageButtons.forEach((button,index)=>{
  const row=rows[index],values=row?[...row.querySelectorAll('dd')].map(d=>d.textContent.trim()).filter(Boolean):[];
  const waiting=values.length===0||values.every(value=>/^(等待|暂无|尚未|未提供)/.test(value));
  button.querySelector('small').textContent=waiting?'未收到':`${values.length} 项数据`;
  button.classList.toggle('has-data',!waiting);button.setAttribute('aria-selected',String(index===selectedStage));button.tabIndex=index===selectedStage?0:-1;
  if(row)row.hidden=index!==selectedStage;
 });
 $('stage-title').textContent=stages[selectedStage].title;
 stagePanel.setAttribute('aria-labelledby','stage-tab-'+selectedStage);
 explorer.querySelector('[data-view=diff]').hidden=selectedStage!==2;
 explorer.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.view===view)));
 $('stage-summary').hidden=view!=='summary';$('stage-diff').hidden=view!=='diff';$('stage-json').hidden=view!=='json';
 let blank=$('stage-summary').querySelector('.inspection-empty');
 if(!rows[selectedStage]){if(!blank){blank=document.createElement('p');blank.className='inspection-empty';$('stage-summary').append(blank);}blank.textContent='本次输入还没有该阶段的数据。';}else blank?.remove();
 const field=$(stages[selectedStage].field),present=hasPayload(field);
 $('stage-json-code').hidden=!present;$('stage-json-empty').hidden=present;
 $('stage-json-caption').textContent=selectedStage===4?'模型、编排、推理及错误原文':selectedStage===2?'current 为当前状态，at_decision / after_playback 属于所选输入':'';
 if(present && $('stage-json-code').textContent!==field.textContent)$('stage-json-code').textContent=field.textContent;
 if(view==='diff')paintDiff();
}
new MutationObserver(paintStage).observe(pipeline,{childList:true,subtree:true,characterData:true});

// Raw payloads become a source browser rather than a long accordion. Original pre
// elements remain in the document and retain their backend-owned IDs and contents.
const raw=$('room-raw'),sourceRoot=raw.querySelector('.jam-debug-data');
const files=[...sourceRoot.querySelectorAll('details')].map(node=>({name:node.querySelector('summary').textContent,pre:node.querySelector('pre')}));
sourceRoot.replaceChildren();sourceRoot.classList.add('payload-browser');
const fileNav=document.createElement('nav');fileNav.className='payload-nav';fileNav.setAttribute('aria-label','原始数据来源');
const fileView=document.createElement('section');fileView.className='payload-view';fileView.innerHTML='<header><h3 id="payload-title">InputEvent</h3><span id="payload-size"></span></header><p id="payload-empty" class="inspection-empty">尚未收到数据</p>';
sourceRoot.append(fileNav,fileView);let selectedFile=1;
const fileButtons=files.map((file,index)=>{const button=document.createElement('button');button.type='button';button.textContent=file.name;button.onclick=()=>{selectedFile=index;paintPayload();};fileNav.append(button);fileView.append(file.pre);new MutationObserver(()=>{paintPayload();paintStage();}).observe(file.pre,{childList:true,subtree:true,characterData:true});return button;});
function paintPayload(){
 files.forEach((file,index)=>{file.pre.hidden=index!==selectedFile||!hasPayload(file.pre);fileButtons[index].setAttribute('aria-current',String(index===selectedFile));fileButtons[index].classList.toggle('has-data',hasPayload(file.pre));});
 const current=files[selectedFile],present=hasPayload(current.pre);$('payload-title').textContent=current.name;$('payload-size').textContent=present?`${new TextEncoder().encode(current.pre.textContent).length.toLocaleString()} bytes`:'';$('payload-empty').hidden=present;
}
const updateContext=()=>{const active=inspector.querySelector('[role=tab][aria-selected=true]')?.id;context.hidden=!['room-tab-trace','room-tab-raw'].includes(active);};
new MutationObserver(updateContext).observe(inspector.querySelector('.inspector-tabs'),{attributes:true,subtree:true,attributeFilter:['aria-selected']});
paintStage();paintPayload();updateContext();
