/* JamMate local audio. Browser-generated preview only; no backend or model events. */
(()=>{
  'use strict';
  let ctx=null, sequence=0, disposed=false;
  const volumes={band:.8,drums:.8,keys:.8,bass:.8}, buses={}, voices=new Map(), groups=new Set();
  const meters={'4/4':{steps:8,division:2,tempoUnit:'quarter'},'3/4':{steps:6,division:2,tempoUnit:'quarter'},'6/8':{steps:6,division:3,tempoUnit:'dotted-quarter'}};
  const preview={playing:false,bpm:96,meter:'4/4',position:0,anchorPosition:0,anchorTime:0,elapsed:0,runStart:0,nextStep:0,onBeat:null,timer:null,generation:0,pending:new Set()};
  const midiPreview={playing:false,generation:0,anchorTime:0,positionMs:0,durationMs:0,sourceDurationMs:0,truncated:false,events:[],cursor:0,timer:null,pending:new Set(),held:new Map(),onNote:null,onEnded:null};
  const EPS=0.000001, horizon=.085, lookahead=25;
  const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
  function validNumber(value,name){const n=Number(value);if(!Number.isFinite(n))throw new TypeError(name+' must be finite');return n}
  function create(){
    if(disposed)disposed=false;
    if(ctx&&ctx.state!=='closed')return ctx;
    const AudioContext=window.AudioContext||window.webkitAudioContext;
    if(!AudioContext)throw new Error('此浏览器不支持 Web Audio');
    ctx=new AudioContext({latencyHint:'interactive'});
    buses.band=ctx.createGain();buses.band.gain.value=volumes.band*.62;buses.band.connect(ctx.destination);
    for(const id of ['drums','keys','bass']){buses[id]=ctx.createGain();buses[id].gain.value=volumes[id];buses[id].connect(buses.band)}
    return ctx;
  }
  async function ensure(){const a=create();if(a.state==='suspended'||a.state==='interrupted')await a.resume();if(a.state!=='running')throw new Error('音频设备尚未启动，请再次点击播放');return a}
  function context(){return ctx&&ctx.state!=='closed'?ctx:null}
  function setVolume(target,value){if(!(target in volumes))throw new Error('Unknown audio target: '+target);const v=clamp(validNumber(value,'Volume'),0,1);volumes[target]=v;const a=context();if(a&&buses[target]){const p=buses[target].gain;p.cancelScheduledValues(a.currentTime);p.setTargetAtTime(v*(target==='band'?.62:1),a.currentTime,.012)}return v}
  function createVoice(note,velocity=90,source='screen',when){
    const n=validNumber(note,'MIDI note'),v=clamp(validNumber(velocity,'MIDI velocity'),0,127);if(n<0||n>127)throw new RangeError('MIDI note must be 0–127');
    const a=create(),token='voice-'+(++sequence),o=a.createOscillator(),g=a.createGain(),f=a.createBiquadFilter(),t=Math.max(a.currentTime,when??a.currentTime),isBass=String(source).startsWith('bass'),peak=(isBass?.24:.18)*Math.pow(v/127,1.15);
    // Keep the local instrument inexpensive: one oscillator with a soft attack and
    // decay, rather than a permanently flat test tone. This is not a sampled piano.
    o.type='triangle';o.frequency.setValueAtTime(440*Math.pow(2,(n-69)/12),t);f.type='lowpass';f.frequency.setValueAtTime(isBass?500+v*4:2200+v*24,t);f.Q.value=.45;
    g.gain.setValueAtTime(.0001,t);g.gain.linearRampToValueAtTime(Math.max(.0001,peak),t+.009);g.gain.exponentialRampToValueAtTime(Math.max(.0001,peak*(isBass?.72:.32)),t+(isBass?.22:.85));
    if(!isBass)g.gain.exponentialRampToValueAtTime(Math.max(.0001,peak*.12),t+4);
    o.connect(f);f.connect(g);g.connect(buses[isBass?'bass':'keys']);
    const voice={token,o,g,f,note:n,source,released:false,startedAt:t,peak};voices.set(token,voice);o.onended=()=>{voices.delete(token);try{o.disconnect();f.disconnect();g.disconnect()}catch{}};o.start(t);return token;
  }
  function noteOn(note,velocity=90,source='screen'){return createVoice(note,velocity,source)}
  function releaseVoice(token,when,force=false){const v=voices.get(token);if(!v||(v.released&&!force))return;v.released=true;const t=Math.max(ctx.currentTime,when??ctx.currentTime);if(v.startedAt>t){v.g.gain.cancelScheduledValues(t);v.g.gain.setValueAtTime(0,t);try{v.o.stop(t)}catch{}return}if(typeof v.g.gain.cancelAndHoldAtTime==='function')v.g.gain.cancelAndHoldAtTime(t);else v.g.gain.cancelScheduledValues(t);v.g.gain.setTargetAtTime(0,t,.026);try{v.o.stop(t+.18)}catch{}}
  function noteOff(token){releaseVoice(token)}
  function releaseAll(){for(const token of [...voices.keys()])releaseVoice(token,undefined,true)}
  function makeGroup(kind,time,isPreview){const a=create(),g=a.createGain();g.connect(buses.drums);const group={kind,time,isPreview,g,nodes:[],remaining:0,done:false};groups.add(group);return group}
  function cleanGroup(group){if(group.done)return;group.done=true;groups.delete(group);for(const node of group.nodes){try{node.disconnect()}catch{}}try{group.g.disconnect()}catch{}}
  function addSource(group,node,end){group.nodes.push(node);group.remaining++;node.onended=()=>{group.remaining--;if(group.remaining<=0)cleanGroup(group)};node.start(group.time);node.stop(end)}
  function oscillator(group,type,freq,endFreq,duration,level){const a=ctx,o=a.createOscillator(),g=a.createGain(),t=group.time;o.type=type;o.frequency.setValueAtTime(freq,t);if(endFreq)o.frequency.exponentialRampToValueAtTime(endFreq,t+duration*.72);g.gain.setValueAtTime(Math.max(.0001,level),t);g.gain.exponentialRampToValueAtTime(.0001,t+duration);o.connect(g);g.connect(group.g);group.nodes.push(g);addSource(group,o,t+duration+.025)}
  function noise(group,duration,frequency,level,bandpass=false){const a=ctx,b=a.createBuffer(1,Math.ceil(a.sampleRate*(duration+.03)),a.sampleRate),d=b.getChannelData(0);for(let i=0;i<d.length;i++)d[i]=Math.random()*2-1;const s=a.createBufferSource(),f=a.createBiquadFilter(),g=a.createGain(),t=group.time;s.buffer=b;f.type=bandpass?'bandpass':'highpass';f.frequency.value=frequency;f.Q.value=bandpass?.7:.5;g.gain.setValueAtTime(level,t);g.gain.exponentialRampToValueAtTime(.0001,t+duration);s.connect(f);f.connect(g);g.connect(group.g);group.nodes.push(f,g);addSource(group,s,t+duration+.02)}
  function playDrum(kind,when,isPreview=false){
    const a=create(),aliases=['kick','snare','hat','perc'];if(typeof kind==='number')kind=aliases[((kind%4)+4)%4];if(!aliases.includes(kind))throw new Error('Unknown drum sound: '+kind);
    const time=when===undefined?a.currentTime:Math.max(a.currentTime,validNumber(when,'Audio time')),g=makeGroup(kind,time,isPreview);
    if(kind==='kick')oscillator(g,'sine',142,43,.27,.78);
    else if(kind==='snare'){noise(g,.14,1550,.24);oscillator(g,'triangle',190,113,.09,.12)}
    else if(kind==='hat')noise(g,.043,7200,.12);
    else{oscillator(g,'sine',720,340,.105,.27);oscillator(g,'triangle',1040,690,.035,.045)}
    return g;
  }
  function drum(kind,when){return playDrum(kind,when,false)}
  function stopGroup(group){if(group.done)return;const t=ctx.currentTime;try{group.g.gain.cancelScheduledValues(t);group.g.gain.setValueAtTime(0,t)}catch{}for(const node of group.nodes){if(typeof node.stop==='function')try{node.stop(t)}catch{}}cleanGroup(group)}
  function stepPeriod(){return 60/preview.bpm/meters[preview.meter].division}
  function positionAt(time){return preview.playing?preview.anchorPosition+Math.max(0,time-preview.anchorTime)/stepPeriod():preview.position}
  function elapsedAt(time){return preview.elapsed+(preview.playing?Math.max(0,time-preview.runStart):0)}
  function cancelPending(futureOnly=false){const t=context()?.currentTime||0;preview.generation++;for(const entry of preview.pending){clearTimeout(entry.timer)}preview.pending.clear();for(const group of [...groups])if(group.isPreview&&(!futureOnly||group.time>t+.002))stopGroup(group)}
  function queueBeat(step,time,bpm){
    const meter=preview.meter,definition=meters[meter],inBar=step%definition.steps,accent=inBar%definition.division===0;
    const sounds=['hat'];
    if(meter==='4/4'){if(inBar===0||inBar===3)sounds.unshift('kick');if(inBar===4)sounds.unshift('snare')}
    else if(meter==='3/4'){if(inBar===0)sounds.unshift('kick');if(inBar===2||inBar===4)sounds.unshift('snare')}
    else{if(inBar===0)sounds.unshift('kick');if(inBar===3)sounds.unshift('snare')}
    for(const sound of sounds){const group=playDrum(sound,time,true);group.g.gain.value=sound==='hat'?(accent?1:.48):(sound==='snare'&&meter==='3/4'?.78:1)}
    // bar, beat and subdivision are one-based; step remains zero-based.
    const musical={meter,tempoUnit:definition.tempoUnit,bar:Math.floor(step/definition.steps)+1,beat:Math.floor(inBar/definition.division)+1,subdivision:inBar%definition.division+1,subdivisionsPerBeat:definition.division,beatsPerBar:definition.steps/definition.division,accent};
    const entry={step,time,bpm,sounds,generation:preview.generation,timer:null};preview.pending.add(entry);
    const notify=()=>{if(!preview.playing||entry.generation!==preview.generation){preview.pending.delete(entry);return}const a=context();if(!a){preview.pending.delete(entry);return}if(a.state!=='running'||a.currentTime<time-.001){entry.timer=setTimeout(notify,Math.max(8,Math.min(25,(time-a.currentTime)*1000)));return}preview.pending.delete(entry);if(a.currentTime-time>.15)return;try{preview.onBeat?.({source:'local-preview',step,bpm,time,...musical,sounds:[...sounds],elapsedMs:Math.round(elapsedAt(time)*1000)})}catch(e){console.error('JamMate preview callback:',e)}};
    entry.timer=setTimeout(notify,Math.max(0,(time-ctx.currentTime)*1000));
  }
  function schedule(){
    if(!preview.playing||!context()||ctx.state!=='running')return;
    const t=ctx.currentTime,period=stepPeriod();
    let nextTime=preview.anchorTime+(preview.nextStep-preview.anchorPosition)*period;
    // After a throttled timer, skip missed notes. Never emit a catch-up burst.
    if(nextTime<t-.018){preview.nextStep=Math.ceil(positionAt(t)+EPS);nextTime=preview.anchorTime+(preview.nextStep-preview.anchorPosition)*period}
    let count=0;while(nextTime<t+horizon&&count++<4){queueBeat(preview.nextStep,Math.max(t,nextTime),preview.bpm);preview.nextStep++;nextTime=preview.anchorTime+(preview.nextStep-preview.anchorPosition)*period}
  }
  function validatedTempo(value){const n=validNumber(value,'BPM');if(n<30||n>250)throw new RangeError('BPM 必须在 30–250 之间');return n}
  function validatedMeter(value){if(!Object.prototype.hasOwnProperty.call(meters,value))throw new RangeError('拍号必须为 4/4、3/4 或 6/8');return value}
  function startPreview(value=preview.bpm,options={}){
    const n=validatedTempo(value),meter=options.meter===undefined?preview.meter:validatedMeter(options.meter),a=create();if(a.state!=='running')throw new Error('请先启动音频设备');
    if(typeof options.onBeat==='function')preview.onBeat=options.onBeat;
    if(preview.playing){setTempo(n);if(meter!==preview.meter)setMeter(meter);return getPreviewState()}
    if(meter!==preview.meter)setMeter(meter);
    preview.bpm=n;preview.playing=true;preview.anchorPosition=preview.position;preview.anchorTime=a.currentTime+.012;preview.runStart=a.currentTime;preview.nextStep=Math.ceil(preview.position-EPS);preview.generation++;schedule();clearInterval(preview.timer);preview.timer=setInterval(schedule,lookahead);return getPreviewState();
  }
  function pausePreview(){if(!preview.playing)return getPreviewState();const t=context()?.currentTime||preview.runStart;preview.position=positionAt(t);preview.elapsed=elapsedAt(t);preview.playing=false;clearInterval(preview.timer);preview.timer=null;cancelPending(false);return getPreviewState()}
  function stopPreview(){pausePreview();preview.position=0;preview.anchorPosition=0;preview.nextStep=0;preview.elapsed=0;return getPreviewState()}
  function setTempo(value){const n=validatedTempo(value);if(n===preview.bpm)return n;if(preview.playing){const t=ctx.currentTime,p=positionAt(t);cancelPending(true);preview.anchorPosition=p;preview.anchorTime=t;preview.nextStep=Math.floor(p+EPS)+1;preview.bpm=n;schedule()}else preview.bpm=n;return n}
  function setMeter(value){
    const meter=validatedMeter(value);if(meter===preview.meter)return{...getPreviewState(),applied:'unchanged',barRestarted:false};
    const t=context()?.currentTime||0,p=positionAt(t),nextBar=Math.max(0,Math.ceil((p-EPS)/meters[preview.meter].steps));
    // Local changes apply immediately, at a fresh bar. They do not wait for the
    // old bar to finish. Cancel both future attacks and old tails; elapsed time
    // remains continuous. A paused preview resumes at that fresh bar.
    cancelPending(false);preview.meter=meter;preview.position=nextBar*meters[meter].steps;preview.anchorPosition=preview.position;preview.nextStep=preview.position;
    if(preview.playing){preview.anchorTime=t+.012;schedule()}
    return{...getPreviewState(),applied:'immediate',barRestarted:true};
  }
  function getPreviewState(){const t=context()?.currentTime||0,step=Math.floor(positionAt(t)+EPS),definition=meters[preview.meter],inBar=step%definition.steps;return{playing:preview.playing,step,bpm:preview.bpm,meter:preview.meter,tempoUnit:definition.tempoUnit,bar:Math.floor(step/definition.steps)+1,beat:Math.floor(inBar/definition.division)+1,subdivision:inBar%definition.division+1,subdivisionsPerBeat:definition.division,beatsPerBar:definition.steps/definition.division,elapsedMs:Math.round(elapsedAt(t)*1000)}}
  function getMidiPreviewState(){return{playing:midiPreview.playing,elapsedMs:Math.round(midiPreview.playing?clamp(((context()?.currentTime||midiPreview.anchorTime)-midiPreview.anchorTime)*1000,0,midiPreview.durationMs):midiPreview.positionMs),durationMs:midiPreview.durationMs,sourceDurationMs:midiPreview.sourceDurationMs,truncated:midiPreview.truncated}}
  function stopMidiPreview(reason='stopped'){
    const wasPlaying=midiPreview.playing,endCallback=midiPreview.onEnded;
    midiPreview.positionMs=getMidiPreviewState().elapsedMs;midiPreview.playing=false;midiPreview.generation++;clearInterval(midiPreview.timer);midiPreview.timer=null;
    for(const pending of midiPreview.pending)clearTimeout(pending.timer);midiPreview.pending.clear();midiPreview.held.clear();
    for(const voice of [...voices.values()])if(String(voice.source).startsWith('midi-preview:'))releaseVoice(voice.token,undefined,true);
    for(const group of [...groups])if(group.isMidiPreview)stopGroup(group);
    midiPreview.onEnded=null;midiPreview.onNote=null;
    if(wasPlaying&&typeof endCallback==='function')try{endCallback({...getMidiPreviewState(),reason})}catch(e){console.error('JamMate MIDI callback:',e)}
    return getMidiPreviewState();
  }
  function midiNotify(event,time,generation){
    if(typeof midiPreview.onNote!=='function')return;
    const pending={timer:null};midiPreview.pending.add(pending);
    const notify=()=>{if(!midiPreview.playing||generation!==midiPreview.generation){midiPreview.pending.delete(pending);return}const a=context();if(!a){midiPreview.pending.delete(pending);return}if(a.state!=='running'||a.currentTime<time-.001){pending.timer=setTimeout(notify,20);return}midiPreview.pending.delete(pending);if(a.currentTime-time>.15)return;try{midiPreview.onNote({...event,source:'local-midi-preview',audioTime:time})}catch(e){console.error('JamMate MIDI callback:',e)}};
    pending.timer=setTimeout(notify,Math.max(0,(time-ctx.currentTime)*1000));
  }
  function scheduleMidi(){
    if(!midiPreview.playing||!context()||ctx.state!=='running')return;
    const now=ctx.currentTime,end=midiPreview.anchorTime+midiPreview.durationMs/1000;
    if(now>=end){midiPreview.positionMs=midiPreview.durationMs;stopMidiPreview(midiPreview.truncated?'limit':'ended');return}
    const generation=midiPreview.generation;
    while(midiPreview.cursor<midiPreview.events.length){
      const e=midiPreview.events[midiPreview.cursor],time=midiPreview.anchorTime+e.timestampMs/1000;
      if(time>now+horizon)break;midiPreview.cursor++;
      const key=e.track+':'+e.channel+':'+e.note,isOn=e.kind==='note_on'&&e.velocity>0;
      // On return from a throttled/background tab, drop missed attacks, but still
      // release their matching voices. Do not replay the backlog as a burst.
      if(isOn){
        if(time<now-.03)continue;
        if(e.channel===10){const kind=[35,36].includes(e.note)?'kick':[38,40].includes(e.note)?'snare':[42,44,46].includes(e.note)?'hat':'perc';const group=playDrum(kind,time,false);group.isMidiPreview=true;group.g.gain.value=e.velocity/127}
        else{if(voices.size>=96){const oldest=[...voices.values()].find(v=>String(v.source).startsWith('midi-preview:')&&!v.released);if(oldest)releaseVoice(oldest.token,undefined,true)}const token=createVoice(e.note,e.velocity,'midi-preview:'+generation,time);if(!midiPreview.held.has(key))midiPreview.held.set(key,[]);midiPreview.held.get(key).push(token)}
      }else{const held=midiPreview.held.get(key);if(held?.length){releaseVoice(held.shift(),time);if(!held.length)midiPreview.held.delete(key)}}
      midiNotify(e,Math.max(time,now),generation);
    }
  }
  async function startMidiPreview(events,options={}){
    if(!Array.isArray(events)||events.length>50000)throw new Error('MIDI 试听最多支持 50,000 个音符事件');
    // Public/imported event channels are 1–16; General MIDI percussion is channel 10.
    const normalized=events.map((e,index)=>{const timestampMs=Number(e.timestampMs),note=Number(e.note),velocity=Number(e.velocity??(e.kind==='note_on'?90:0)),channel=Number(e.channel??1),track=Number(e.track??0);if(!['note_on','note_off'].includes(e.kind)||!Number.isFinite(timestampMs)||timestampMs<0||!Number.isInteger(note)||note<0||note>127||!Number.isFinite(velocity)||velocity<0||velocity>127||!Number.isInteger(channel)||channel<1||channel>16||!Number.isFinite(track))throw new Error('MIDI 试听事件格式无效');return{kind:e.kind,note,velocity,timestampMs,channel,track,index}}).sort((a,b)=>a.timestampMs-b.timestampMs||a.index-b.index);
    if(!normalized.some(e=>e.kind==='note_on'&&e.velocity>0))throw new Error('MIDI 中没有可试听的音符');
    stopMidiPreview('replaced');const generation=midiPreview.generation;
    const a=await ensure();if(generation!==midiPreview.generation)return getMidiPreviewState();
    const sourceDurationMs=Math.max(normalized.at(-1).timestampMs+240,500),durationMs=Math.min(120000,sourceDurationMs);
    midiPreview.events=normalized.filter(e=>e.timestampMs<durationMs);midiPreview.sourceDurationMs=sourceDurationMs;midiPreview.durationMs=durationMs;midiPreview.truncated=sourceDurationMs>120000;midiPreview.positionMs=0;midiPreview.cursor=0;midiPreview.anchorTime=a.currentTime+.025;midiPreview.onNote=typeof options.onNote==='function'?options.onNote:null;midiPreview.onEnded=typeof options.onEnded==='function'?options.onEnded:null;midiPreview.playing=true;
    scheduleMidi();midiPreview.timer=setInterval(scheduleMidi,lookahead);return getMidiPreviewState();
  }
  async function dispose(){stopPreview();stopMidiPreview('disposed');releaseAll();for(const group of [...groups])stopGroup(group);for(const voice of voices.values()){try{voice.o.stop()}catch{}}voices.clear();for(const bus of Object.values(buses))try{bus.disconnect()}catch{}const a=ctx;ctx=null;disposed=true;if(a&&a.state!=='closed')await a.close()}
  window.JMAudio=Object.freeze({ensure,noteOn,noteOff,releaseAll,setVolume,drum,startPreview,pausePreview,stopPreview,setTempo,setMeter,getPreviewState,startMidiPreview,stopMidiPreview,getMidiPreviewState,context,dispose});
})();

/* File inspection is local and read-only. Imported text is never evaluated.
   Container checks establish format, not transcription or successful playback.
   MIDI timestamps use the file's tempo map; they do not change the session BPM. */
(()=>{
  'use strict';
  const LIMITS={text:1024*1024,midi:8*1024*1024,audio:40*1024*1024,events:50000,tracks:256};
  const extOf=name=>(String(name).match(/\.([^.]+)$/)?.[1]||'').toLowerCase();
  const ascii=(bytes,start,count)=>String.fromCharCode(...bytes.subarray(start,start+count));
  const fail=message=>{throw new Error(message)};
  function formatBytes(value){const n=Number(value);if(!Number.isFinite(n)||n<0)return '—';if(n<1024)return Math.round(n)+' B';if(n<1024*1024)return (n/1024).toFixed(n<10240?1:0)+' KB';return (n/(1024*1024)).toFixed(n<10485760?1:0)+' MB'}
  const seconds=ms=>ms<60000?(ms/1000).toFixed(1)+' 秒':Math.floor(ms/60000)+' 分 '+Math.floor(ms/1000)%60+' 秒';
  function textFile(bytes,ext){
    let text;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes)}catch{fail('文字文件不是有效 UTF-8 编码，请先另存为 UTF-8 的 TXT 或 Markdown。')}
    if(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(text))fail('文件含有二进制或异常控制字符，不能作为文字指令导入。');
    if(!text.trim())fail('文字文件为空，请选择包含指令的文件。');
    return {kind:'text',mime:ext==='md'?'text/markdown':'text/plain',text,summary:(ext==='md'?'Markdown':'文字')+' · '+[...text].length+' 字符'};
  }
  function midiFile(bytes){
    const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),size=bytes.length;
    const need=(pos,len,end=size)=>{if(!Number.isInteger(len)||len<0||pos<0||pos+len>end)fail('MIDI 文件被截断，或事件长度超出轨道边界。')};
    const u16=pos=>{need(pos,2);return view.getUint16(pos)},u32=pos=>{need(pos,4);return view.getUint32(pos)};
    if(size<14||ascii(bytes,0,4)!=='MThd')fail('文件不是有效的标准 MIDI 文件（缺少 MThd 头）。');
    const headerLength=u32(4);if(headerLength<6)fail('MIDI 文件头长度无效。');need(8,headerLength);
    const format=u16(8),trackCount=u16(10),division=u16(12);
    if(format===2)fail('暂不支持 Format 2 的独立序列 MIDI，请导出为 Format 0 或 Format 1。');
    if(format!==0&&format!==1)fail('MIDI 格式编号无效，仅支持 Format 0 和 Format 1。');
    if(division&0x8000)fail('暂不支持 SMPTE 时间制 MIDI，请导出为 PPQ 拍点时间制。');
    if(!division)fail('MIDI 的 PPQ 分辨率不能为 0。');
    if(trackCount<1||trackCount>LIMITS.tracks)fail('MIDI 轨道数无效或过多（最多 '+LIMITS.tracks+' 轨）。');
    if(format===0&&trackCount!==1)fail('Format 0 MIDI 必须恰好包含一条轨道。');
    const events=[],tempoEvents=[],timeSignatures=[],tracks=[];let offset=8+headerLength,eventCount=0,order=0;
    for(let track=0;track<trackCount;track++){
      need(offset,8);if(ascii(bytes,offset,4)!=='MTrk')fail('第 '+(track+1)+' 轨缺少 MTrk 标记，文件可能损坏。');
      const length=u32(offset+4),end=offset+8+length;need(offset+8,length);let pos=offset+8,tick=0,running=0,ended=false;
      const info={index:track,name:'',channels:[],noteCount:0,eventCount:0,endTick:0,durationMs:0};const channels=new Set();
      const byte=()=>{need(pos,1,end);return bytes[pos++]};
      const vlq=()=>{let value=0;for(let n=0;n<4;n++){const b=byte();value=value*128+(b&127);if(!(b&128))return value}fail('MIDI 可变长度数值超过 4 字节，文件损坏。')};
      const dataByte=()=>{const b=byte();if(b&128)fail('MIDI 通道事件的数据字节无效。');return b};
      while(pos<end){
        if(++eventCount>LIMITS.events)fail('MIDI 事件过多（最多 '+LIMITS.events.toLocaleString('en-US')+' 条），请裁剪后再导入。');
        info.eventCount++;tick+=vlq();if(!Number.isSafeInteger(tick))fail('MIDI 的时间位置超出可解析范围。');
        let status;need(pos,1,end);if(bytes[pos]&128)status=byte();else{if(!running)fail('MIDI Running Status 缺少对应的通道状态。');status=running}
        const ordinal=order++;
        if(status>=0x80&&status<=0xEF){
          running=status;const command=status&0xF0,channel=(status&15)+1,a=dataByte(),b=command===0xC0||command===0xD0?null:dataByte();channels.add(channel);
          if(command===0x90||command===0x80){const kind=command===0x90&&b>0?'note_on':'note_off';if(kind==='note_on')info.noteCount++;events.push({kind,note:a,velocity:b,tick,channel,track,source:'file',_order:ordinal})}
        }else if(status===0xFF){
          running=0;const type=byte(),len=vlq();need(pos,len,end);
          if(type===0x2F){if(len!==0)fail('MIDI 轨道结束标记长度无效。');ended=true;for(let p=pos;p<end;p++)if(bytes[p]!==0)fail('MIDI 轨道结束标记后仍有未声明数据。');pos=end;break}
          if(type===0x03){info.name=new TextDecoder('utf-8').decode(bytes.subarray(pos,pos+Math.min(len,512))).replace(/[\u0000-\u001F\u007F]/g,' ').slice(0,120)}
          if(type===0x51){if(len!==3)fail('MIDI 速度事件必须为 3 字节。');const us=bytes[pos]*65536+bytes[pos+1]*256+bytes[pos+2];if(!us)fail('MIDI 速度事件不能为 0。');tempoEvents.push({tick,microsecondsPerQuarter:us,bpm:60000000/us,track,provided:true,_order:ordinal})}
          if(type===0x58){if(len!==4||bytes[pos]===0||bytes[pos+1]>30)fail('MIDI 拍号事件无效。');timeSignatures.push({tick,numerator:bytes[pos],denominator:2**bytes[pos+1],clocksPerMetronome:bytes[pos+2],thirtySecondsPerQuarter:bytes[pos+3],track,_order:ordinal})}
          pos+=len;
        }else if(status===0xF0||status===0xF7){running=0;const len=vlq();need(pos,len,end);pos+=len}
        else fail('MIDI 包含不支持或损坏的系统事件：0x'+status.toString(16).toUpperCase()+'。');
      }
      if(!ended)fail('第 '+(track+1)+' 轨缺少结束标记，文件可能被截断。');
      info.endTick=tick;info.channels=[...channels].sort((a,b)=>a-b);tracks.push(info);offset=end;
    }
    if(offset!==size)fail('MIDI 声明的轨道以外还有额外数据，请重新导出标准 MIDI。');
    if(!tracks.some(t=>t.noteCount>0))fail('MIDI 中没有可导入的音符，只含控制或元数据。');
    const compare=(a,b)=>a.tick-b.tick||a.track-b.track||a._order-b._order;
    tempoEvents.sort(compare);events.sort(compare);timeSignatures.sort(compare);
    const tempos=[{tick:0,microsecondsPerQuarter:500000,bpm:120,track:null,provided:false,timestampMs:0}];
    for(const e of tempoEvents){const t={...e};delete t._order;if(tempos.at(-1).tick===t.tick)tempos[tempos.length-1]=t;else tempos.push(t)}
    tempos[0].timestampMs=0;
    for(let i=1;i<tempos.length;i++){const prev=tempos[i-1];tempos[i].timestampMs=prev.timestampMs+(tempos[i].tick-prev.tick)*prev.microsecondsPerQuarter/division/1000}
    function msAt(tick){let lo=0,hi=tempos.length-1;while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(tempos[mid].tick<=tick)lo=mid;else hi=mid-1}const t=tempos[lo],ms=t.timestampMs+(tick-t.tick)*t.microsecondsPerQuarter/division/1000;if(!Number.isFinite(ms)||ms>Number.MAX_SAFE_INTEGER)fail('MIDI 时长超出可解析范围。');return Math.round(ms*1000)/1000}
    for(const e of events){e.timestampMs=msAt(e.tick);delete e._order}
    for(const s of timeSignatures){s.timestampMs=msAt(s.tick);delete s._order}
    for(const t of tracks)t.durationMs=msAt(t.endTick);
    const durationMs=Math.max(...tracks.map(t=>t.durationMs)),tempoProvided=tempoEvents.length>0,noteCount=tracks.reduce((n,t)=>n+t.noteCount,0);
    return{kind:'midi',mime:'audio/midi',format,ppq:division,tempoProvided,tempos,timeSignatures,tracks,events,durationMs,summary:`MIDI · ${tracks.length} 轨 · ${noteCount} 个音 · ${seconds(durationMs)}`+(tempoProvided?'':' · 未声明速度，按 120 BPM 换算')};
  }
  function audioFile(bytes,ext){
    const len=bytes.length,view=new DataView(bytes.buffer,bytes.byteOffset,len);
    let container='',mime='';
    if(len>=12&&ascii(bytes,0,4)==='RIFF'&&ascii(bytes,8,4)==='WAVE'){
      container='wav';mime='audio/wav';let pos=12,fmt=false,data=false;const declared=view.getUint32(4,true);if(declared!==0xFFFFFFFF&&declared+8>len)fail('WAV 文件被截断。');
      while(pos+8<=len){const tag=ascii(bytes,pos,4),n=view.getUint32(pos+4,true);if(pos+8+n>len)fail('WAV 音频块长度超过文件边界。');if(tag==='fmt '){if(n<16)fail('WAV 格式块不完整。');fmt=true}if(tag==='data'&&n>0)data=true;pos+=8+n+(n%2)}
      if(!fmt||!data)fail('WAV 缺少有效的格式信息或音频数据。');
    }else if(len>=12&&ascii(bytes,4,4)==='ftyp'){
      const box=view.getUint32(0);if(box<16||box>len)fail('M4A 容器头不完整。');container='m4a';mime='audio/mp4';
    }else if(len>=4&&ascii(bytes,0,4)==='OggS'){
      if(len<27||bytes[4]!==0)fail('Ogg 页面头不完整或版本无效。');const segments=bytes[26];if(len<27+segments)fail('Ogg 分段表被截断。');let packet=0;for(let i=0;i<segments;i++){packet+=bytes[27+i];if(bytes[27+i]<255)break}const start=27+segments;if(!packet||start+packet>len)fail('Ogg 音频头被截断。');const head=ascii(bytes,start,Math.min(8,packet));if(!(head.startsWith('OpusHead')||head.startsWith('\x01vorbis')||head.startsWith('Speex   ')||head.startsWith('\x7FFLAC')||head.startsWith('fLaC')))fail('这个 Ogg 容器未包含可识别的音频头。');container='ogg';mime='audio/ogg';
    }else if(len>=4&&ascii(bytes,0,4)==='fLaC'){
      if(len<42||(bytes[4]&127)!==0||(bytes[5]*65536+bytes[6]*256+bytes[7])!==34)fail('FLAC 的 STREAMINFO 头不完整。');container='flac';mime='audio/flac';
    }else if(len>=4&&bytes[0]===0x1A&&bytes[1]===0x45&&bytes[2]===0xDF&&bytes[3]===0xA3){
      function vint(pos,keepMarker=false){if(pos>=len||!bytes[pos])fail('WebM EBML 头无效。');let width=1,mask=128;while(!(bytes[pos]&mask)){mask>>=1;width++;if(width>8)fail('WebM EBML 长度无效。')}if(pos+width>len)fail('WebM EBML 头被截断。');let v=keepMarker?bytes[pos]:bytes[pos]&(mask-1);for(let i=1;i<width;i++)v=v*256+bytes[pos+i];if(!Number.isSafeInteger(v))fail('WebM EBML 长度超出范围。');return{value:v,width}}
      const header=vint(4),end=4+header.width+header.value;if(end>len||end>8192)fail('WebM EBML 头不完整或过大。');let pos=4+header.width,docType='';while(pos<end){const id=vint(pos,true);pos+=id.width;const size=vint(pos);pos+=size.width;if(pos+size.value>end)fail('WebM EBML 子元素被截断。');if(id.value===0x4282)docType=ascii(bytes,pos,Math.min(size.value,16));pos+=size.value}if(docType!=='webm')fail('这是 EBML 容器，但不是 WebM 文件。');container='webm';mime='audio/webm';
    }else if(len>=4&&(ascii(bytes,0,3)==='ID3'||(bytes[0]===255&&(bytes[1]&224)===224))){
      let start=0;if(ascii(bytes,0,3)==='ID3'){if(len<10||bytes[3]<2||bytes[3]>4||[6,7,8,9].some(i=>bytes[i]&128))fail('MP3 的 ID3 头无效。');start=10+bytes[6]*2097152+bytes[7]*16384+bytes[8]*128+bytes[9]+(bytes[3]===4&&(bytes[5]&16)?10:0);if(start>=len)fail('MP3 只有标签或音频数据被截断。')}
      let valid=false;for(let p=start;p<=Math.min(len-4,start+65536);p++){
        if(bytes[p]!==255||(bytes[p+1]&224)!==224)continue;const version=(bytes[p+1]>>3)&3,layer=(bytes[p+1]>>1)&3,bitrateIndex=(bytes[p+2]>>4)&15,rateIndex=(bytes[p+2]>>2)&3;
        if(version===1||layer!==1||bitrateIndex===0||bitrateIndex===15||rateIndex===3)continue;
        const rates=[44100,48000,32000],bitrates=version===3?[0,32,40,48,56,64,80,96,112,128,160,192,224,256,320]:[0,8,16,24,32,40,48,56,64,80,96,112,128,144,160];const rate=rates[rateIndex]/(version===3?1:version===2?2:4),frame=Math.floor((version===3?144000:72000)*bitrates[bitrateIndex]/rate)+((bytes[p+2]>>1)&1);if(p+frame<=len){valid=true;break}
      }
      if(!valid)fail('MP3 没有完整的 MPEG Layer III 音频帧。');container='mp3';mime='audio/mpeg';
    }
    if(!container)fail('无法识别音频容器；请确认文件没有损坏或仅被改名。');
    if(container!==ext)fail('文件扩展名 .'+ext+' 与实际 '+container.toUpperCase()+' 容器不符，请使用正确的文件扩展名。');
    return{kind:'audio',mime,container,summary:container.toUpperCase()+' 音频 · '+formatBytes(len)};
  }
  async function parseFile(file){
    if(!file||typeof file.arrayBuffer!=='function'||typeof file.name!=='string'||!Number.isFinite(file.size))fail('没有收到有效文件，请重新选择。');
    const ext=extOf(file.name),kind=['txt','md'].includes(ext)?'text':['mid','midi'].includes(ext)?'midi':['wav','mp3','m4a','ogg','flac','webm'].includes(ext)?'audio':null;
    if(!kind)fail('暂不支持此文件类型。请选择 TXT、MD、MID/MIDI，或 WAV、MP3、M4A、OGG、FLAC、WEBM。');
    if(!file.size)fail('文件为空，请选择有效内容。');if(file.size>LIMITS[kind])fail((kind==='text'?'文字':kind==='midi'?'MIDI':'音频')+'文件过大，上限为 '+formatBytes(LIMITS[kind])+'。');
    let buffer;try{buffer=await file.arrayBuffer()}catch{fail('无法读取文件，请重新选择或检查文件是否仍可访问。')}
    const bytes=new Uint8Array(buffer);if(bytes.length!==file.size||bytes.length>LIMITS[kind])fail('文件大小在读取过程中发生变化，请重新选择。');
    const result=kind==='text'?textFile(bytes,ext):kind==='midi'?midiFile(bytes):audioFile(bytes,ext);
    return{...result,file,name:file.name,size:bytes.length};
  }
  window.JMImports=Object.freeze({parseFile,formatBytes});
})();

/* Canonical display state from backend receipts only. Preserve raw receipts in Inspector. */
function mergeJMMusicState(previous, patch) {
  const next={...previous,...patch};
  const own=(o,k)=>Object.prototype.hasOwnProperty.call(o,k);
  if(!own(patch,'chord')&&own(patch,'currentChord'))next.chord=patch.currentChord;
  if(!own(patch,'progression')&&own(patch,'chordProgression'))next.progression=patch.chordProgression;
  if((own(patch,'progression')||own(patch,'chordProgression'))&&!own(patch,'progressionIndex'))next.progressionIndex=null;
  if((own(patch,'chord')||own(patch,'currentChord'))&&!own(patch,'progressionIndex'))next.progressionIndex=null;
  // Explicit volumes are normalized gain updates. A new track snapshot supersedes stale per-track gains.
  if(own(patch,'tracks')||own(patch,'layers')){
    const raw=patch.tracks??patch.layers;
    const entries=Array.isArray(raw)?raw.filter(t=>t&&typeof t==='object').map(t=>[t.id??t.track??t.name,t]):raw&&typeof raw==='object'?Object.entries(raw):[];
    const gain={};if(Number.isFinite(previous.volumes?.band))gain.band=previous.volumes.band;
    for(const [id,t] of entries)if(typeof id==='string'&&typeof t?.volume==='number'&&Number.isFinite(t.volume)&&t.volume>=0&&t.volume<=1)gain[id]=t.volume;
    next.volumes=own(patch,'volumes')?(patch.volumes==null?null:{...gain,...patch.volumes}):gain;
    if(!own(patch,'tracks'))next.tracks=patch.layers;
  }else if(own(patch,'volumes'))next.volumes=patch.volumes==null?null:{...previous.volumes,...patch.volumes};
  return next;
}
/* Real receipts only. The host calls JMInspector.browser(...) for local observations and
   JMInspector.backend(...) for events obtained from its actual backend adapter. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('ins'), trigger = $('insBtn'), handle=$('insResize');
  if (!panel) return;
  const released = new URLSearchParams(location.search).get('release') === '1';
  const noop = () => {};
  if (released) {
    if (trigger) trigger.hidden = true;
    panel.hidden = true;
    window.JMInspector = { browser: noop, backend: noop, connection: noop, open: noop, close: noop, clearBackend: noop, isOpen: () => false };
    return;
  }
  let activeTab = 'trace', selectedTrace = '', selectedFilter = 'all', connected = false, connectionLabel = '', connectionStatus = '',
    sequence = 0, renderTimer = 0, noticeTimer = 0,
    backendState = null, stateTimestamp = null, stateReceived = null, stateReceipts = [], isOpen = false;
  const browserEvents = [], traces = new Map(), openDetails = new Set(), retained = { browser: 300, traces: 80, events: 1200 };
  const tabScroll = { trace: 0, state: 0, browser: 0 };
  const tabIds = { trace: ['insTabTrace','insTracePanel'], state: ['insTabState','insStatePanel'], browser: ['insTabBrowser','insBrowserPanel'] };
  const targetNames = { band: '全乐队', drums: '节奏组', keys: '旋律组', harmony:'和声组', bass: '贝斯' };
  const stageNames = {
    capture:'输入采集', raw_input:'原始输入', input:'输入', recognition:'声音识别', asr:'语音识别',
    transcription:'语音转写', transcript:'识别结果', classify:'输入分类', input_event:'InputEvent',
    inputevent:'InputEvent', jam_state:'JamState', jamstate:'JamState', decision:'指令决策',
    agent:'Agent', arrange:'音乐编排', arranger:'音乐编排', band_plan:'BandPlan', bandplan:'BandPlan',
    model:'模型调用', model_request:'模型请求', model_response:'模型回复', renderer:'声音生成',
    render:'声音生成', schedule:'音符排程', scheduler:'音符排程', playback:'播放回执', harmony_intent:'和声意图', harmony_plan:'和声编排', harmony_applied:'和声生效回执', state:'音乐状态回执', 'agent.reply':'乐队回应', error:'错误'
  };
  const browserNames = {
    target_select:'切换交互目标', 'target.selected':'切换交互目标', pad_hit:'触发鼓垫', note_on:'按下琴键', note_off:'松开琴键',
    volume:'调整音量', bpm_change:'调整速度', bpm:'调整速度', tempo_change:'调整速度', transport:'播放控制',
    mic_start:'开始录音', mic_stop:'结束录音', mic_error:'麦克风错误', mic_permission:'请求麦克风权限',
    command_text:'文字指令', command_not_sent:'指令未发送', command_error:'指令发送失败',
    input_audio:'音频素材', audio_ready:'录音已准备好', audio_sent:'已提交音频', audio_discarded:'移除音频',
    midi_connected:'MIDI 已连接', midi_error:'MIDI 设备错误', midi_capture_start:'开始记录旋律', midi_capture_stop:'完成旋律记录',
    midi_send:'提交旋律', backend_connected:'后端已连接', backend_disconnected:'后端已断开',
    adapter_connected:'接入层已连接', adapter_disconnected:'接入层已断开', adapter_error:'接入层错误',
    action_requested:'提交操作', action_sent:'操作已交给接入层', action_not_sent:'操作未发送', action_error:'操作失败',
    ui_ready:'页面就绪', audio_error:'音频错误', preview_started:'开始本地预览', preview_paused:'暂停本地预览',
    adapter_accepted:'操作已交给接入层', request_error:'操作发送失败', keyboard_octave:'切换键盘音区',
    midi_message:'收到 MIDI 消息', midi_devices:'MIDI 设备列表更新', midi_unavailable:'MIDI 不可用',
    meter_changed:'调整拍号', tempo_changed:'调整速度', mic_unavailable:'麦克风不可用', mic_background_stop:'离开页面，结束录音',
    preview_paused_for_capture:'录音前暂停本地节拍', stale_response:'忽略旧连接回执',
    theme_changed:'切换主题', instrument_focus:'切换乐器视图', input_mode:'选择输入用途',
    file_imported:'导入真实文件', file_error:'文件导入失败', draft_removed:'移除输入素材',
    draft_target:'调整素材目标', draft_mode:'调整素材用途', midi_track:'选择 MIDI 轨道',
    midi_preview:'试听 MIDI 素材', mixer_open:'展开混音台', mixer_close:'收起混音台',
    'input.text':'文字输入', 'input.audio':'音频输入', 'input.midi':'旋律输入'
  };
  const statusNames = { success:'完成', completed:'完成', complete:'完成', done:'完成', running:'处理中', started:'开始',
    start:'开始', pending:'等待', queued:'待处理', error:'错误', failed:'失败', rejected:'拒绝', skipped:'跳过', cancelled:'取消',
    acknowledged:'已确认', accepted:'已接收', received:'已收到', waiting:'等待', playing:'播放中', scheduled:'已排程', applied:'已生效', needs_input:'待补充' };
  function clone(value) {
    const seen = new WeakSet();
    try {
      return JSON.parse(JSON.stringify(value, (_k,v) => {
        if (typeof v === 'bigint') return v.toString();
        if (typeof Blob !== 'undefined' && v instanceof Blob) return { mimeType:v.type, sizeBytes:v.size };
        if (v instanceof Error) return { name:v.name, message:v.message, stack:v.stack };
        if (v && typeof v === 'object') { if (seen.has(v)) return '[Circular]'; seen.add(v); }
        return v;
      }));
    } catch { return { serializationError:'此事件含无法序列化的数据' }; }
  }
  const stringify = v => { try { return JSON.stringify(v, null, 2) ?? 'null'; } catch { return '[无法序列化]'; } };
  const str = v => v == null ? '—' : typeof v === 'object' ? stringify(v) : String(v);
  function node(tag, className, value) { const el = document.createElement(tag); if (className) el.className = className; if (value != null) el.textContent = str(value); return el; }
  function put(id, value) { const el=$(id); if (el) el.textContent = str(value); }
  function time(value, includeMs = false) {
    if (value == null) return '未提供时间';
    const d = new Date(value); if (!Number.isFinite(d.getTime())) return String(value);
    const t = d.toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
    return includeMs ? `${t}.${String(d.getMilliseconds()).padStart(3,'0')}` : t;
  }
  function brief(v, max=110) { const s=typeof v === 'object' ? stringify(v).replace(/\s+/g,' ') : str(v); return s.length>max?s.slice(0,max-1)+'…':s; }
  function notify(s) { put('insNotice',s); clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>put('insNotice',''),2400); }
  function empty(title, message, button) {
    const box=node('div','ins-empty');
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg'); icon.setAttribute('viewBox','0 0 32 32'); icon.setAttribute('fill','none'); icon.setAttribute('stroke-width','1.15'); icon.setAttribute('aria-hidden','true');
    const path=document.createElementNS(icon.namespaceURI,'path'); path.setAttribute('d','M6 8h7l4 4h9v13H6zM11 17h10m-10 4h6');icon.append(path);box.append(icon,node('h3','',title),node('p','',message));
    if(button){const b=node('button','',button.label);b.type='button';b.onclick=button.action;box.append(b)}return box;
  }
  function attachDetails(el,key) { el.dataset.insKey=key;el.open=openDetails.has(key);el.addEventListener('toggle',()=>{if(!el.isConnected)return;if(el.open)openDetails.add(key);else openDetails.delete(key)});return el; }
  function rawDetails(label, data, key) {
    const d = attachDetails(node('details','ins-raw'), key);
    const pre = node('pre','ins-json',stringify(data));
    pre.dataset.insScroll = key;
    pre.tabIndex = 0;
    d.append(node('summary','',label), pre);
    return d;
  }
  function field(label, data, key) {
    const f=node('div','ins-field');f.append(node('p','ins-field-label',label));
    if (typeof data==='object'&&data!==null) {
      const pre = node('pre','ins-json',stringify(data));
      pre.dataset.insScroll = key;
      pre.tabIndex = 0;
      f.append(pre);
    }
    else f.append(node('div','ins-field-value',str(data)));return f;
  }
  function rememberRender(root, render) {
    const scroll=$('insScroll'),top=scroll.scrollTop;
    // Snapshot current DOM state before replacement; a detached detail may still have a queued toggle.
    root.querySelectorAll('details[data-ins-key]').forEach(el=>{if(el.open)openDetails.add(el.dataset.insKey);else openDetails.delete(el.dataset.insKey)});
    const nested = new Map([...root.querySelectorAll('[data-ins-scroll]')].map(el => [
      el.dataset.insScroll, {top: el.scrollTop, left: el.scrollLeft},
    ]));
    const focused=document.activeElement;const focusedDetails=focused?.closest?.('[data-ins-key]');
    const key=focusedDetails?.dataset.insKey;const isSummary=focused?.tagName==='SUMMARY';
    render();scroll.scrollTop=top;
    for (const el of root.querySelectorAll('[data-ins-scroll]')) {
      const saved = nested.get(el.dataset.insScroll);
      if (saved) { el.scrollTop = saved.top; el.scrollLeft = saved.left; }
      if (el.dataset.insScroll === focused?.dataset.insScroll) el.focus({preventScroll:true});
    }
    if(key&&isSummary){const found=[...root.querySelectorAll('[data-ins-key]')].find(x=>x.dataset.insKey===key);found?.querySelector('summary')?.focus({preventScroll:true})}
  }
  function currentTrace(){return selectedTrace&&traces.has(selectedTrace)?traces.get(selectedTrace):[...traces.values()].at(-1)}
  function eventTitle(e){ const raw=e.stage||e.component||e.name||e.type||'事件';return stageNames[String(raw).toLowerCase()]||String(raw); }
  function traceInput(t){
    for(const ev of t.events){const e=ev.raw,d=e.data||{},i=e.input||d.input;
      for(const v of [e.text,typeof i==='string'?i:null,i?.text,d.text,e.transcript,d.transcript])if(typeof v==='string'&&v.trim())return v.trim();
      if(i?.modality==='humming'||i?.mode==='humming')return '哼唱旋律';if(i?.modality==='rhythm'||i?.mode==='rhythm')return '拍手节奏';
      if(i?.modality==='midi'||i?.type==='midi')return 'MIDI 旋律';if(i?.modality==='audio'||i?.type==='audio')return '音频输入';
    }return t.unlinked?'未关联 Trace 的后端事件':'输入处理';
  }
  function duration(e){for(const key of ['durationMs','latencyMs','elapsedMs'])if(Number.isFinite(e[key]))return `${e[key]} ms`;return null}
  function metadata(e){const parts=[];if(e.timestamp!=null)parts.push(time(e.timestamp,true));if(e.model!=null)parts.push(brief(e.model,70));if(e.component!=null&&e.component!==e.stage)parts.push(brief(e.component,40));const d=duration(e);if(d)parts.push(d);return parts.join(' · ')}
  function stageDetails(ev) {
    const e=ev.raw,k=ev.key,status=e.status?statusNames[e.status]||String(e.status):'已收到';
    const d=attachDetails(node('details','ins-step'+(/error|failed|rejected/i.test(String(e.status||e.type))?' is-error':'')+(/running|pending|queued|started/.test(String(e.status))?' is-pending':'')),k);
    const s=node('summary');s.append(node('span','ins-step-title',eventTitle(e)),node('span','ins-step-status',status),node('i','ins-chevron'));d.append(s);
    const meta=metadata(e);if(meta)d.append(node('p','ins-step-line',meta));
    const content=node('div','ins-stage-data');const data=e.data&&typeof e.data==='object'?e.data:{};
    const fields=[['目标',e.target??data.target],['输入',e.input??data.input],['输出',e.output??data.output],['请求',e.request??data.request],['回复',e.response??data.response],['实际音乐状态',e.state],['计划生效小节',e.effectiveBar??data.effectiveBar],['持续小节',e.durationBars??data.durationBars],['错误',e.error??data.error]];
    let fieldsAdded=0;for(const [label,value] of fields)if(value!==undefined){content.append(field(label,value,k+label));fieldsAdded++}
    if(!fieldsAdded&&e.message!==undefined)content.append(field('内容',e.message,k+'message'));
    content.append(rawDetails('完整事件',e,k+'raw'));d.append(content);return d;
  }
  function renderTrace(){
    const root=$('insTraceContent');rememberRender(root,()=>{
      const select=$('insTraceSelect'),old=selectedTrace;select.replaceChildren(node('option','','最新输入'));select.options[0].value='';
      [...traces.values()].reverse().forEach(t=>{const o=node('option','',`${brief(traceInput(t),32)}${t.unlinked?'':` · ${brief(t.id,18)}`}`);o.value=t.key;select.append(o)});
      selectedTrace=old&&traces.has(old)?old:'';select.value=selectedTrace;select.disabled=!traces.size;
      const t=currentTrace();if(!t){root.replaceChildren(empty(connected?'等待处理记录':'等待后端连接',connected?'收到的识别、决策、模型输入输出与播放回执将在这里按输入串联。':'后端处理记录暂时为空。本页操作已独立记录，可在浏览器事件中查看。',{label:'查看浏览器事件',action:()=>setTab('browser')}));return}
      const intro=node('div','ins-trace-intro');if(!connected)intro.append(node('p','ins-history-note','历史记录 · 后端已断开'));intro.append(node('p','ins-input-title',traceInput(t)));
      const meta=node('div','ins-trace-meta');meta.append(node('span','',`${t.events.length} 条后端事件`));
      const first=t.events[0]?.raw, target=first?.target??first?.input?.target??first?.data?.target;
      if(target)meta.append(node('span','',targetNames[target]||target));intro.append(meta);
      if(!t.unlinked)intro.append(node('p','ins-trace-id',`Trace · ${t.id}`));
      const list=node('div','ins-pipeline');t.events.forEach(e=>list.append(stageDetails(e)));root.replaceChildren(intro,list);
      if(t.dropped)root.append(node('p','ins-source-note',`较早的 ${t.dropped} 条事件已从本页缓冲区移除。`));
    });
  }
  function category(e){const t=e.type.toLowerCase();if(e.meta?.level==='error'||/error|fail|reject|denied|unavailable/.test(t))return 'error';if(/mic|device|connect|permission|adapter|midi_device/.test(t))return 'device';if(/note|pad|volume|transport|bpm|tempo|meter|preview|play/.test(t))return 'music';return 'input'}
  function browserPreview(e){const d=e.data||{},a=[];if(d.target)a.push(targetNames[d.target]||d.target);if(d.text)a.push(brief(d.text,100));if(d.midi!=null)a.push(`MIDI ${d.midi}`);if(d.note!=null)a.push(`MIDI ${d.note}`);if(d.bpm!=null)a.push(`${d.bpm} BPM`);if(d.meter)a.push(d.meter+' 拍');if(d.tempoUnit==='dotted-quarter')a.push('BPM 按附点四分音符');if(d.value!=null)a.push(str(d.value));if(d.sound)a.push(d.sound);if(d.pad!=null&&!d.sound)a.push(`鼓垫 ${Number(d.pad)+1}`);if(d.playing!==undefined)a.push(d.playing?'开始':'暂停');if(d.delivered===false||d.reason==='backend_not_connected')a.push('未发送到后端');if(d.seconds!=null)a.push(`${d.seconds} s`);else if(d.durationMs!=null)a.push(`${(d.durationMs/1000).toFixed(1)} s`);if(d.octave!=null)a.push(`C${d.octave} 起`);if(d.notes!=null)a.push(`${d.notes} 个起音`);if(d.bytes!=null)a.push(`${Math.round(d.bytes/1024)} KB`);if(Array.isArray(d.inputs))a.push(`${d.inputs.filter(i=>i.state==='connected').length} 个设备已连接`);if(d.type)a.push(d.type);if(d.source==='local-preview')a.push('本地试听');if(d.source==='requested')a.push('请求值，待后端确认');if(d.message)a.push(brief(d.message,90));if(d.error)a.push(brief(d.error,90));return a.join(' · ')}
  function visibleBrowser(){return browserEvents.filter(e=>selectedFilter==='all'||category(e)===selectedFilter)}
  function renderBrowser(){const root=$('insBrowserContent');rememberRender(root,()=>{const events=visibleBrowser();if(!events.length){root.replaceChildren(empty('暂无记录',selectedFilter==='all'?'操作乐器、调整速度或输入指令后，会在这里出现真实浏览器事件。':'此分类下还没有记录。'));return}root.replaceChildren(...events.slice().reverse().map(e=>{const d=attachDetails(node('details','ins-browser-event'+(category(e)==='error'?' is-error':'')),e.key),s=node('summary');s.append(node('span','ins-event-time',time(e.timestamp)),node('span','ins-event-name',browserNames[e.type]||e.type.replaceAll('_',' ')),node('i','ins-chevron'));d.append(s);const preview=browserPreview(e);if(preview)d.append(node('p','ins-event-preview',preview));const data=node('div','ins-stage-data');data.append(node('p','ins-field-label',`${e.type} · ${time(e.timestamp,true)} · 浏览器`),node('pre','ins-json',stringify({...e.meta,source:'browser',timestamp:e.timestamp,type:e.type,data:e.data})));d.append(data);return d}))})}
  function tracksOf(state){const t=state.tracks??state.layers;if(Array.isArray(t))return t.map((x,i)=>typeof x==='object'?x:{name:x});if(t&&typeof t==='object')return Object.entries(t).map(([id,value])=>typeof value==='object'?{id,...value}:{id,value});return []}
  function renderRoll(notes,root){
    const valid=notes.filter(n=>Number.isFinite(n.pitch??n.midi)&&Number.isFinite(n.startBeat)&&Number.isFinite(n.durationBeats)&&n.durationBeats>0);
    if(!valid.length){root.append(node('p','ins-roll-note','已收到音符数据，但缺少可绘图的 pitch、startBeat 或 durationBeats；完整数据可在下方查看。'));return}
    const wrap=node('div','ins-roll-wrap'),canvas=node('canvas','ins-roll');canvas.width=980;canvas.height=328;canvas.setAttribute('role','img');canvas.setAttribute('aria-label',`后端提供的 ${valid.length} 个排程音符`);wrap.append(canvas);root.append(wrap);
    const ctx=canvas.getContext('2d');if(!ctx)return;const start=Math.min(0,...valid.map(n=>n.startBeat)),end=Math.max(4,...valid.map(n=>n.startBeat+n.durationBeats));const low=Math.min(...valid.map(n=>n.pitch??n.midi)),high=Math.max(...valid.map(n=>n.pitch??n.midi));const padding=22,row=284/Math.max(12,high-low+1);const x=b=>padding+(b-start)/(end-start)*(980-2*padding);
    ctx.fillStyle='#141310';ctx.fillRect(0,0,980,328);ctx.strokeStyle='#2b261f';ctx.lineWidth=1;
    const gridStep=Math.max(1,Math.ceil((end-start)/16));for(let b=Math.ceil(start);b<=end;b+=gridStep){ctx.beginPath();ctx.moveTo(x(b),12);ctx.lineTo(x(b),304);ctx.stroke()}
    for(let p=0;p<=Math.max(12,high-low+1);p++){const y=14+p*row;ctx.beginPath();ctx.moveTo(padding,y);ctx.lineTo(958,y);ctx.stroke()}
    const palette=['#d0ae80','#94b4a6','#b0a3c3','#c78d73'];const tracks=[...new Set(valid.map(n=>String(n.track??n.instrument??'')))];valid.forEach(n=>{const track=String(n.track??n.instrument??'');ctx.fillStyle=palette[tracks.indexOf(track)%palette.length];const y=16+(high-(n.pitch??n.midi))*row;ctx.fillRect(x(n.startBeat),y,Math.max(2,x(n.startBeat+n.durationBeats)-x(n.startBeat)-2),Math.max(3,row-3))});
    root.append(node('p','ins-roll-note',`${valid.length} 个音符 · ${start}–${end} 拍 · 仅绘制后端提供的排程数据`));
  }
  function renderState(){const root=$('insStateContent');rememberRender(root,()=>{
    if(!backendState){root.replaceChildren(empty('等待音乐状态','BPM、和弦、声部和音符以真实后端回执为准。本地预览不会填入这些字段。'));return}
    const state=backendState,frag=document.createDocumentFragment(),meta=node('div','ins-state-meta');if(!connected)frag.append(node('p','ins-history-note','历史状态 · 后端已断开，以下是断开前最后收到的回执。'));meta.append(node('span','',state.phase?`状态 · ${statusNames[state.phase]||({ready:'就绪',playing:'演奏中',paused:'已暂停',ended:'已结束'}[state.phase]||state.phase)}`:'后端音乐状态'),node('span','',stateTimestamp!=null?`后端时间 ${time(stateTimestamp)}`:`接收于 ${time(stateReceived)}`));frag.append(meta);
    const metrics=node('dl','ins-metrics');[['BPM',state.bpm],['拍号',state.meter],['调性',state.key],['当前和弦',state.chord],['小节',state.bar??state.currentBar]].forEach(([label,value])=>{const group=node('div');group.append(node('dt','',label),node('dd','',value??'—'));metrics.append(group)});frag.append(metrics);
    const progression=state.progression;frag.append(node('h3','ins-section-title','和弦进行'),node('p','ins-progression',Array.isArray(progression)&&progression.length?progression.map(str).join('  →  '):typeof progression==='string'&&progression.trim()?progression:'—'));if(Number.isInteger(state.progressionIndex)&&Array.isArray(progression)&&state.progressionIndex>=0&&state.progressionIndex<progression.length)frag.append(node('p','ins-source-note','当前进行位置 · '+(state.progressionIndex+1)));frag.append(node('p','ins-source-note','仅展示已确认的音乐状态；和声请求与计划见处理过程。'));
    const tracks=tracksOf(state);if(tracks.length){frag.append(node('h3','ins-section-title','声部'));const wrap=node('div','ins-table-wrap'),table=node('table'),head=node('thead'),tr=node('tr');['声部','音量','密度','状态'].forEach(t=>tr.append(node('th','',t)));head.append(tr);table.append(head);const body=node('tbody');tracks.forEach(t=>{const row=node('tr'),id=t.name??t.id??t.track??'—',volume=t.volume??t.gain??t.volumeDb,density=t.density,stateText=t.status??(typeof t.muted==='boolean'?(t.muted?'静音':'启用'):typeof t.enabled==='boolean'?(t.enabled?'启用':'关闭'):'—');[targetNames[id]||id,volume==null?'—':t.volumeDb!=null&&t.volume==null&&t.gain==null?`${volume} dB`:str(volume),density,stateText].forEach(v=>row.append(node('td','',v??'—')));body.append(row)});table.append(body);wrap.append(table);frag.append(wrap)}
    const notes=state.scheduledNotes??state.notes;if(Array.isArray(notes)&&notes.length){frag.append(node('h3','ins-section-title','实际排程音符'));renderRoll(notes,frag);frag.append(rawDetails('音符原始数据',notes,'state-notes'))}
    for(const [key,label] of [['playbackReceipt','播放回执'],['bandPlan','BandPlan'],['inputEvent','InputEvent']])if(state[key]!=null)frag.append(rawDetails(label,state[key],'state-'+key));
    frag.append(rawDetails('完整音乐状态',state,'state-raw'));root.replaceChildren(frag);
  })}
  function updateCounts(){put('insTraceCount',traces.size);put('insBrowserCount',browserEvents.length);const total=[...traces.values()].reduce((n,t)=>n+t.events.length,0);put('insRetention',`后端 ${total} 条 · 浏览器 ${browserEvents.length} 条`)}
  function render(){renderTimer=0;updateCounts();if(!isOpen)return;if(activeTab==='trace')renderTrace();else if(activeTab==='state')renderState();else renderBrowser();$('insCopy').disabled=activeTab==='trace'?!currentTrace():activeTab==='state'?!backendState:!visibleBrowser().length}
  function schedule(){updateConnectionHeader();updateCounts();if(isOpen&&!renderTimer)renderTimer=setTimeout(render,80)}
  function setTab(tab){if(!tabIds[tab])return;tabScroll[activeTab]=$('insScroll').scrollTop;activeTab=tab;for(const [key,[button,pane]]of Object.entries(tabIds)){$(button).setAttribute('aria-selected',String(key===tab));$(button).tabIndex=key===tab?0:-1;$(pane).hidden=key!==tab}render();$('insScroll').scrollTop=tabScroll[tab]}
  function prune(){while(traces.size>retained.traces){const oldest=traces.keys().next().value;const removed=traces.get(oldest);removed.events.forEach(e=>{openDetails.delete(e.key);openDetails.delete(e.key+'raw')});traces.delete(oldest);if(selectedTrace===oldest)selectedTrace=''}let total=[...traces.values()].reduce((n,t)=>n+t.events.length,0);for(const t of traces.values()){while(total>retained.events&&t.events.length>1){const e=t.events.shift();openDetails.delete(e.key);openDetails.delete(e.key+'raw');t.dropped=(t.dropped||0)+1;total--}if(total<=retained.events)break}}
  function receiveBrowser(type,data={},meta={}){const e={key:`browser-${++sequence}`,type:String(type),timestamp:new Date().toISOString(),data:clone(data),meta:clone(meta)};browserEvents.push(e);while(browserEvents.length>retained.browser){const removed=browserEvents.shift();openDetails.delete(removed.key)}schedule();return e.key}
  function receiveBackend(event){if(!event||typeof event!=='object')return;const e=clone(event),receivedAt=new Date().toISOString();
    if(e.type==='state'&&e.state&&typeof e.state==='object'){backendState=mergeJMMusicState(backendState||{},e.state);stateTimestamp=e.timestamp??e.state.timestamp??null;stateReceived=receivedAt;stateReceipts.push(e);if(stateReceipts.length>30)stateReceipts.shift();if(activeTab==='state')schedule();if(!(e.traceId??e.trace_id??e.requestId??e.request_id))return}
    if(e.type==='capabilities'||e.type==='connection')return;
    const request=e.requestId??e.request_id;const id=e.traceId??e.trace_id??request,unlinked=id==null||id==='';const linked=request!=null?[...traces.values()].find(t=>t.events.some(ev=>(ev.raw.requestId??ev.raw.request_id)===request)):null;const key=linked?.key??(unlinked?'__unlinked__':String(id));
    if(!traces.has(key))traces.set(key,{key,id:unlinked?null:String(id),unlinked,events:[]});const t=traces.get(key);t.events.push({key:`backend-${++sequence}`,raw:e,receivedAt});prune();schedule();
  }
  function updateConnectionHeader(){const s=$('insConnection'),history=!connected&&(traces.size>0||backendState!==null);s.classList.toggle('connected',connected);s.querySelector('span').textContent=connected?(connectionLabel?`已连接 · ${connectionLabel}`:'已连接后端'):`${connectionStatus||'未连接后端'}${history?' · 历史记录已保留':''}`;}
  function connection(info={}){connected=!!info.connected;connectionLabel=info.label?String(info.label):'';connectionStatus=info.status?String(info.status):'';updateConnectionHeader();schedule()}
  function clearBackend(){traces.clear();selectedTrace='';backendState=null;stateTimestamp=null;stateReceived=null;stateReceipts=[];tabScroll.trace=0;tabScroll.state=0;for(const key of [...openDetails])if(key.startsWith('backend-')||key.startsWith('state-'))openDetails.delete(key);$('insTraceContent').replaceChildren();$('insStateContent').replaceChildren();updateConnectionHeader();schedule();}

  function open(){if(isOpen)return;isOpen=true;document.body.classList.add('inspector-open');panel.inert=false;panel.setAttribute('aria-hidden','false');panel.classList.add('open');trigger?.setAttribute('aria-expanded','true');fitInspector();render();}
  function close(){if(!isOpen)return;const returnFocus=panel.contains(document.activeElement);isOpen=false;document.body.classList.remove('inspector-open');trigger?.setAttribute('aria-expanded','false');if(returnFocus)trigger?.focus({preventScroll:true});panel.inert=true;panel.setAttribute('aria-hidden','true');panel.classList.remove('open');}
  const narrow=()=>matchMedia('(max-width:760px)').matches;
  let insWidth=400,insHeight=0,drag=null;
  function fitInspector(){const mobile=narrow();handle.setAttribute('aria-orientation',mobile?'horizontal':'vertical');const maxW=Math.max(280,Math.min(640,innerWidth-460));const w=Math.min(maxW,Math.max(280,insWidth));const h=Math.min(innerHeight*.7,Math.max(Math.min(200,innerHeight*.4),insHeight||innerHeight*.4));document.body.style.setProperty('--ins-w',w+'px');document.body.style.setProperty('--ins-h',h+'px');handle.setAttribute('aria-valuemin',String(mobile?Math.round(Math.min(200,innerHeight*.4)):280));handle.setAttribute('aria-valuemax',String(mobile?Math.round(innerHeight*.7):maxW));handle.setAttribute('aria-valuenow',String(Math.round(mobile?h:w)));}
  handle.addEventListener('pointerdown',e=>{if(e.button!==0)return;e.preventDefault();drag={id:e.pointerId};handle.setPointerCapture(e.pointerId);handle.classList.add('dragging')});
  handle.addEventListener('pointermove',e=>{if(drag?.id!==e.pointerId)return;if(narrow())insHeight=innerHeight-e.clientY;else insWidth=innerWidth-e.clientX;fitInspector()});
  const endResize=e=>{if(drag?.id===e.pointerId){drag=null;handle.classList.remove('dragging')}};handle.addEventListener('pointerup',endResize);handle.addEventListener('pointercancel',endResize);handle.addEventListener('lostpointercapture',endResize);
  handle.addEventListener('keydown',e=>{const mobile=narrow(),negative=mobile?'ArrowUp':'ArrowLeft',positive=mobile?'ArrowDown':'ArrowRight';if(e.key!==negative&&e.key!==positive)return;e.preventDefault();const amount=e.key===negative?20:-20;if(mobile)insHeight=panel.getBoundingClientRect().height+amount;else insWidth=panel.getBoundingClientRect().width+amount;fitInspector()});
  window.addEventListener('resize',()=>{if(isOpen)fitInspector()});
  async function copy(){let data;if(activeTab==='trace'){const t=currentTrace();if(!t)return;data={source:'backend',traceId:t.id,events:t.events.map(x=>x.raw)}}else if(activeTab==='state'){if(!backendState)return;data={source:'backend',state:backendState,receipts:stateReceipts}}else data={source:'browser',events:visibleBrowser().map(({key,...rest})=>rest)};
    const value=stringify(data);try{if(!navigator.clipboard?.writeText)throw new Error('clipboard unavailable');await navigator.clipboard.writeText(value);notify('已复制')}catch{const t=document.createElement('textarea');t.value=value;t.style.cssText='position:fixed;opacity:0;pointer-events:none;';panel.append(t);t.select();let ok=false;try{ok=document.execCommand('copy')}catch{}t.remove();$('insCopy').focus({preventScroll:true});notify(ok?'已复制':'复制受限，请展开原始数据手动复制')}
  }
  trigger?.setAttribute('aria-controls','ins');trigger?.setAttribute('aria-expanded','false');trigger?.addEventListener('click',()=>isOpen?close():open());$('insClose').addEventListener('click',close);$('insCopy').addEventListener('click',copy);
  document.addEventListener('keydown',event=>{if(isOpen&&panel.contains(event.target)&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();close()}},true);
  panel.addEventListener('keydown',e=>{if(e.key!=='Tab')e.stopPropagation()});
  for(const [tab,[id]]of Object.entries(tabIds)){$(id).addEventListener('click',()=>setTab(tab));$(id).addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const list=Object.keys(tabIds),i=list.indexOf(tab),next=e.key==='Home'?list[0]:e.key==='End'?list.at(-1):list[(i+(e.key==='ArrowRight'?1:-1)+list.length)%list.length];setTab(next);$(tabIds[next][0]).focus()})}
  $('insTraceSelect').addEventListener('change',e=>{selectedTrace=e.target.value;renderTrace();$('insScroll').scrollTop=0;render()});$('insBrowserFilter').addEventListener('change',e=>{selectedFilter=e.target.value;renderBrowser();$('insScroll').scrollTop=0;render()});$('insClearBrowser').addEventListener('click',()=>{for(const e of browserEvents)openDetails.delete(e.key);browserEvents.length=0;schedule();notify('已清空浏览器记录')});
  window.JMInspector={browser:receiveBrowser,backend:receiveBackend,connection,clearBackend,open,close,isOpen:()=>isOpen};connection({connected:false});updateCounts();
})();

(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const audio = window.JMAudio, inspector = window.JMInspector, imports = window.JMImports;
  const ids = ['drums','keys','harmony'];
  const names = {band:'全乐队',drums:'节奏组',keys:'旋律组',harmony:'和声组',bass:'贝斯'};
  const modes = {auto:'自动',speech:'说话',humming:'哼唱',rhythm:'拍手'};
  const themes = {burgundy:'酒红',orange:'橙色',sage:'鼠尾草绿'};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const release = new URLSearchParams(location.search).get('release') === '1';
  const uid = () => crypto.randomUUID?.() || 'jm-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  const mod = (v,n=3) => (v%n+n)%n;
  const bpmValid = v => typeof v === 'number' && Number.isFinite(v) && v>=30 && v<=250;
  const meterValid = v => ['4/4','3/4','6/8'].includes(v);
  const tempoUnit = meter => meter==='6/8'?'dotted-quarter':'quarter';
  const performingSignals = new Set(['note_on','note_off','pad_hit','mic_start','mic_stop','midi_capture_start','midi_capture_stop']);
  const log = (type,data={},meta={}) => {
    if(performingSignals.has(type))window.dispatchEvent(new CustomEvent('jammate:studio-activity',{detail:type}));
    return inspector.browser(type,data,meta);
  };
  const create = (tag,cls,text) => {const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=String(text);return el};
  const objects={}, volumes={band:.8,drums:.8,keys:.8,bass:.8}, lastVolumes={drums:.8,keys:.8,bass:.8};
  let commandTarget='band', inputMode='auto', focusIndex=1, progress=1, frameId=0, frameTime=0;
  let localBpm=96, localMeter='4/4', previewStarted=false, backend=null, backendEpoch=0, unsubscribe=null, state={}, capabilities={};
  let wheelTempoDraft=null,wheelTempoTimer=0;
  let transportPending=null, tempoPending=null, meterPending=null, sending=false, sendTicket=0, importing=false, toastTimer=0, hintTimer=0;
  let octave=4, whiteCount=14, melodyCapture=false, melodyStart=0, melodyEvents=[], melodyDown=new Map();
  let sounding=new Map(), notePointers=new Map(), typingHeld=new Map(), fileNotes=new Set();
  let midiAccess=null, midiDevices=new Set(), micSession=null, micPending=false, autoWakePending=false, waveId=0, micTimer=0, micLimit=0;
  let wakeDisabled=false;
  let drafts=[], previewDraftId=null, previewGeneration=0, selectedDraftId=null, clarification=null, taps=[], swipe=null, wheelStamp=0;
  const focused = () => ids[mod(focusIndex)];
  const online = () => backend !== null;
  const icon = kind => '<svg viewBox="0 0 24 24" aria-hidden="true">'+({
    play:'<path d="m8 5 11 7-11 7z" fill="currentColor" stroke="none"/>',
    pause:'<path d="M9 5v14m7-14v14" stroke-width="3"/>',
    stop:'<rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor" stroke="none"/>',
    wait:'<circle cx="12" cy="12" r="7"/><path d="M12 8v4l3 2"/>',
    mic:'<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3m-3 0h6"/>',
    close:'<path d="m6 6 12 12M18 6 6 18"/>'
  }[kind]||'')+'</svg>';
  function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4200)}
  const agentMessages=[];
  function showAgentReply(event){const message=typeof event.message==='string'?event.message:typeof event.text==='string'?event.text:null;if(!message?.trim())return;const id=event.messageId??event.replyId??event.id??null;const status=({completed:'已完成',applied:'已完成',scheduled:'已安排',running:'执行中',playing:'执行中',pending:'待确认',clarification:'待补充',needs_input:'待补充',error:'未完成',failed:'未完成'})[event.status]||'乐队';const row={id,message:message.trim(),status};if(id!=null){const i=agentMessages.findIndex(x=>x.id===id);if(i>=0)agentMessages.splice(i,1)}agentMessages.unshift(row);agentMessages.splice(12);const node=r=>{const el=create('div','agent-reply-row');el.append(create('span','reply-state',r.status),create('span','reply-message',r.message));return el};$('agentReplies').hidden=false;$('agentLatest').replaceChildren(node(agentMessages[0]));$('agentHistory').hidden=agentMessages.length<2;$('agentHistoryList').replaceChildren(...agentMessages.slice(1).map(node));}
  function hint(message,hold=0){clearTimeout(hintTimer);$('inputHint').textContent=message;if(hold)hintTimer=setTimeout(updateHint,hold)}
  function updateHint(){
    if(micSession)return;
    if(inputMode==='humming'&&commandTarget==='drums')hint('哼唱将作为鼓组的节奏参考');
    else if(inputMode==='rhythm'&&['keys','bass'].includes(commandTarget))hint('拍手提供节奏，音高沿用已有音乐材料');
    else if(commandTarget==='harmony'&&inputMode==='rhythm')hint('拍手只提供和声变化的节奏参考，不自动补充和弦');
    else if(commandTarget==='harmony'&&inputMode==='humming')hint('哼唱提供音高素材，和声调整由乐队理解你的意图');
    else if(!online())hint('本地试听 · 指令与素材将在接入后处理');
    else if(inputMode==='speech')hint(!capabilities.voiceWake?'当前服务未启用语音唤醒':
      wakeDisabled?'本次语音唤醒已暂停':'授权后可直接说「Hey JamMate」或声部名字');
    else hint(commandTarget==='band'?'对全乐队输入，由乐队理解你的意图':'本次输入给'+names[commandTarget]);
  }
  function setTheme(name,remember=true){
    if(!themes[name])return;
    document.documentElement.dataset.theme=name;
    document.querySelectorAll('.theme-dot').forEach(b=>{const selected=b.dataset.theme===name;b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected))});
    if(remember){try{localStorage.setItem('jammate-theme',name)}catch{}log('theme_changed',{theme:name})}
  }
  function setTarget(id){
    if(!names[id])return;
    commandTarget=id;$('targetChip').hidden=id==='band';$('targetName').textContent=names[id];
    $('targetChip').querySelector('.target-icon').innerHTML=id==='drums'?'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9.3 5v14m5.4-14v14M5 9.3h14M5 14.7h14"/>':id==='harmony'?'<path d="M4 5h16v14H4zM7 9h10M7 15h3m2 0h5"/>':id==='bass'?'<circle cx="12" cy="12" r="8"/><path d="m12 12 4-4"/>':'<rect x="3" y="6" width="18" height="13" rx="1.5"/><path d="M8 7v6m4-6v6m4-6v6M7 13v5m5-5v5m5-5v5"/>';
    $('txt').placeholder=id==='band'?'告诉乐队你的想法…':id==='harmony'?'例如：让和声更梦幻一点…':'告诉'+names[id]+'你的想法…';
    $('txt').setAttribute('aria-label','给'+names[id]+'的指令');
    $('dock').classList.toggle('general',id==='band');
    for(const d of drafts)if(d.kind==='text')d.target=id;
    document.querySelectorAll('.draft-text-target').forEach(el=>el.textContent='文字提示 · 跟随输入对象：'+names[id]);
    document.querySelectorAll('[data-object]').forEach(el=>el.classList.toggle('command-target',el.dataset.object===id));
    updateHint();log('target_select',{target:id,focusedInstrument:focused()});
  }
  function setMode(mode){
    if(!modes[mode]||micSession||micPending)return;
    inputMode=mode;
    $('inputModes').querySelectorAll('button').forEach(b=>{const chosen=b.dataset.mode===mode;b.classList.toggle('selected',chosen);b.setAttribute('aria-pressed',String(chosen))});
    updateRecording();updateHint();log('input_mode',{mode});
  }
  function phase(){return online()?(state.phase||'unknown'):(audio.getPreviewState().playing?'playing':previewStarted?'paused':'ready')}
  function updateTransport(){
    const p=phase(),playing=p==='playing';
    document.body.classList.toggle('playing',playing);
    $('play').innerHTML=icon(transportPending?'wait':playing?'pause':'play');
    $('play').disabled=!!transportPending;
    $('play').setAttribute('aria-pressed',String(playing));
    $('play').setAttribute('aria-label',transportPending?'等待播放回执':playing?'暂停演奏':p==='paused'?'继续演奏':online()?'开始演奏':'播放本地节拍');
    $('tempoValue').textContent=online()?(bpmValid(state.bpm)?String(state.bpm):'—'):String(localBpm);
    const labels={ready:'未开始',playing:'正在演奏',paused:'已暂停',starting:'正在准备',pausing:'正在暂停',ended:'已结束',error:'发生错误'};
    $('sessionStatus').textContent=transportPending?(transportPending.action==='pause'?'暂停待确认':'等待播放'):(labels[p]||'等待状态');
    if($('sourceLabel'))$('sourceLabel').textContent=meterPending?meterPending.meter+' 拍待确认':tempoPending?tempoPending.bpm+' BPM 待确认':wheelTempoDraft!==null?wheelTempoDraft+' BPM 待提交':online()?'已连接乐队':'本地试听';
    updateMeter();updateHarmony();updateMixControls();renderMixer();
  }
  function updateMeter(){
    const actual=online()?(meterValid(state.meter)?state.meter:null):localMeter;
    document.querySelectorAll('[data-meter]').forEach(b=>{const selected=b.dataset.meter===actual;b.classList.toggle('selected',selected);b.classList.toggle('pending',b.dataset.meter===meterPending?.meter);b.setAttribute('aria-pressed',String(selected));b.disabled=!!meterPending});
    const unit=actual==='6/8'?'附点四分音符':'四分音符';
    $('tempoBtn').title='滚轮调节 BPM · 点击展开'+(actual?' · '+actual+' · 以'+unit+'为一拍':'');
    $('tempoUnit').textContent=actual==='6/8'?'BPM · ♩.':'BPM';
    $('meterContext').textContent=actual==='6/8'?'6/8 · 每小节两大拍，每拍三个八分音符；BPM 按大拍计数。':actual?actual+' · BPM 以四分音符为一拍。':'拍号等待后端回执。';
  }
  async function applyMeter(meter){
    if(!meterValid(meter)||meterPending)return;
    if(online()){
      if(capabilities.meter===false){toast('当前后端暂不支持调整拍号');return}
      const requestId=uid();meterPending={requestId,meter};updateTransport();
      const ok=await dispatch({type:'meter.set',requestId,meter,tempoUnit:tempoUnit(meter)});
      if(!ok&&meterPending?.requestId===requestId){meterPending=null;updateTransport()}
    }else{
      localMeter=meter;const result=audio.setMeter(meter);updateTransport();
      log('meter_changed',{meter,tempoUnit:tempoUnit(meter),applied:result.applied,barRestarted:result.barRestarted,source:'local-preview'});
      hint(meter==='6/8'?'6/8：两大拍，每拍三分 · BPM 按大拍计数':meter+'：每个四分音符为一拍',6000);
    }
  }
  function summarize(value){
    if(value instanceof Blob)return{mimeType:value.type,sizeBytes:value.size,...(value instanceof File?{name:value.name}:{})};
    if(Array.isArray(value))return value.length>256?{count:value.length,preview:value.slice(0,128).map(summarize),truncated:true}:value.map(summarize);
    if(value&&typeof value==='object'){const out={};for(const [k,v]of Object.entries(value))out[k]=summarize(v);return out}
    return value;
  }
  async function dispatch(action){
    const request={...action,requestId:action.requestId||uid(),timestamp:new Date().toISOString()};
    const voice = request.type === 'voice.turn';
    const details = voice ? {type: request.type, wakeTarget: request.wakeTarget,
      durationMs: Math.round(request.samples.length * 1000 / request.sampleRate)} : summarize(request);
    log('action_requested',details,{requestId:request.requestId});
    const active=backend,epoch=backendEpoch;
    if(!active){log('action_not_sent',{requestId:request.requestId,type:request.type,reason:'backend_not_connected'});return false}
    try{
      const result = await (active.dispatch||active.send).call(active,request);
      if(backend!==active||backendEpoch!==epoch){log('stale_response',{requestId:request.requestId});return false}
      log('adapter_accepted',{requestId:request.requestId,type:request.type});return voice ? result : true;
    }catch(error){
      if(backend!==active||backendEpoch!==epoch)return false;
      log('request_error',{requestId:request.requestId,message:String(error.message||error)},{level:'error'});
      if(transportPending?.requestId===request.requestId)transportPending=null;
      if(tempoPending?.requestId===request.requestId)tempoPending=null;
      if(meterPending?.requestId===request.requestId)meterPending=null;
      updateTransport();
      if (!voice || request.current()) toast((voice ? '语音指令未提交：' : '发送失败，输入已保留：')+String(error.message||error));
      return false;
    }
  }
  function receive(event){
    if(!online()||!event||typeof event!=='object')return;
    if(event.type==='state'&&event.state&&typeof event.state==='object'){
      if(['ended','error','ready'].includes(event.state.phase))tempoPending=meterPending=null;
      state=mergeJMMusicState(state,event.state);
      const receiptId=event.requestId||event.state.requestId;
      if(transportPending&&(!receiptId||receiptId===transportPending.requestId)){
        const expected={start:'playing',resume:'playing',pause:'paused'}[transportPending.action];
        if(event.state.phase===expected||['error','ended'].includes(event.state.phase))transportPending=null;
      }
      if(bpmValid(event.state.bpm)){
        localBpm=event.state.bpm;
        if(tempoPending&&event.state.bpm===tempoPending.bpm&&(!receiptId||receiptId===tempoPending.requestId))tempoPending=null;
      }
      if(meterValid(event.state.meter)){
        localMeter=event.state.meter;
        if(meterPending&&event.state.meter===meterPending.meter&&(!receiptId||receiptId===meterPending.requestId))meterPending=null;
      }
      if(event.state.volumes)for(const id of Object.keys(volumes))if(validGain(event.state.volumes[id]))setVolume(id,event.state.volumes[id]);
      for(const [id,p] of mixPending){if(receiptId===p.requestId&&validGain(confirmedGain(id)))mixPending.delete(id);else if(!receiptId&&confirmedGain(id)===p.value)mixPending.delete(id)}
      updateTransport();
    }else if(event.type==='capabilities'&&event.capabilities){capabilities={...capabilities,...event.capabilities};updateMixControls();renderMixer()}
    else if(event.type==='error'){
      if(!event.requestId||event.requestId===transportPending?.requestId)transportPending=null;
      if(!event.requestId||event.requestId===tempoPending?.requestId)tempoPending=null;
      if(!event.requestId||event.requestId===meterPending?.requestId)meterPending=null;
      for(const [id,p] of mixPending)if(!event.requestId||event.requestId===p.requestId)mixPending.delete(id);
      updateTransport();if(typeof event.message==='string')showAgentReply({...event,status:'error'});toast(String(event.message||'后端返回错误，请查看 Inspector'));
    }else if(event.type==='voice.wake'){
      if (ids.includes(event.target)) moveTo(nearest(event.target));
      else if (event.target === 'band') setTarget('band');
    }else if(event.type==='transcript'&&typeof event.text==='string'){
      if (micSession?.wake && micSession.busy) {
        micSession.caption = '已听到，乐队正在处理…';
        updateRecording();
      }
      hint('识别到：'+event.text);
      if(event.requiresConfirmation)showClarification({requestId:event.requestId,message:event.text,options:[{id:'accept',label:'确认识别结果'},{id:'retry',label:'重新输入'}]});
    }else if(event.type==='clarification'){if(Array.isArray(event.options)&&event.options.length)showClarification(event);else showAgentReply({...event,status:'clarification'})}
    else if(['agent.reply','agent.response','user.message','agent_reply','agent.message'].includes(event.type))showAgentReply(event);
    else if(event.type==='input.interpretation'){
      const mode=modes[event.mode]||event.mode||'输入',target=names[event.target]||event.target;
      hint('识别为'+mode+(target?' · 给'+target:''),7000);
    }else if(event.type==='connection'&&event.connected===false){disconnect();return}
    inspector.backend(event);
    if(event.type==='state'&&!tempoPending&&wheelTempoDraft!==null){clearTimeout(wheelTempoTimer);wheelTempoTimer=setTimeout(flushWheelTempo,160)}
    if(event.type==='error'&&!tempoPending){clearTimeout(wheelTempoTimer);wheelTempoDraft=null}
  }
  function connect(adapter){
    if(!adapter||!(typeof adapter.dispatch==='function'||typeof adapter.send==='function'))throw new TypeError('Adapter needs dispatch(action) or send(action).');
    disconnect(false);audio.stopPreview();audio.stopMidiPreview();previewStarted=false;inspector.clearBackend();
    backend=adapter;const epoch=backendEpoch;state={};capabilities=adapter.capabilities||{};
    inspector.connection({connected:true,label:adapter.label||'乐队',status:'等待后端回执'});
    if(typeof adapter.subscribe==='function'){
      try{const dispose=adapter.subscribe(e=>{if(backend===adapter&&backendEpoch===epoch)receive(e)});if(backend===adapter&&backendEpoch===epoch)unsubscribe=typeof dispose==='function'?dispose:null;else if(typeof dispose==='function')dispose()}
      catch(error){if(backend===adapter&&backendEpoch===epoch)disconnect(false);throw error}
    }
    updateTransport();updateHint();log('adapter_connected',{label:adapter.label||'adapter',capabilities});
    void autoStartWake(true);
    return()=>{if(backend===adapter&&backendEpoch===epoch)disconnect()};
  }
  function disconnect(announce=true){
    if (micSession?.wake) closeWakeMic(micSession);
    const connected=online(),dispose=unsubscribe;clearTimeout(wheelTempoTimer);wheelTempoDraft=null;backendEpoch++;backend=null;unsubscribe=null;
    try{dispose?.()}catch{}state={};capabilities={};mixPending.clear();transportPending=null;tempoPending=null;meterPending=null;sending=false;sendTicket++;syncComposer();clearClarification();audio.setMeter(localMeter);audio.setTempo(localBpm);
    inspector.connection({connected:false,status:'后端未连接'});agentMessages.length=0;$('agentReplies').hidden=true;$('agentLatest').replaceChildren();$('agentHistoryList').replaceChildren();$('agentHistory').hidden=true;updateTransport();updateHint();
    if(announce&&connected){log('adapter_disconnected',{});toast('乐队已断开，输入草稿仍保留')}
  }
  const api={connect,disconnect,receive,send:dispatch,get connected(){return online()},
    getState(){return{source:online()?'backend':'local-preview',focusedInstrument:focused(),target:commandTarget,inputMode,...(online()?state:audio.getPreviewState()),bpm:online()?state.bpm:localBpm,meter:online()?state.meter:localMeter,tempoUnit:online()?(state.tempoUnit||(meterValid(state.meter)?tempoUnit(state.meter):undefined)):tempoUnit(localMeter),tempoPending:tempoPending?.bpm??null,meterPending:meterPending?.meter??null}},
    getConfig(){return{bpm:localBpm,meter:localMeter,tempoUnit:tempoUnit(localMeter),target:commandTarget,focusedInstrument:focused(),inputMode,theme:document.documentElement.dataset.theme,volumes:{...volumes}}},
    isPerforming(){return Boolean((micSession && (!micSession.wake || micSession.busy || micSession.hearing || micSession.target))
      ||micPending||melodyCapture||sounding.size||importing||sending||previewDraftId)},
    getRecordedMelody(){return melodyEvents.map(e=>({...e}))},
    getDrafts(){return drafts.map(d=>({id:d.id,name:d.name,kind:d.kind,target:d.target,mode:d.mode,size:d.size,durationMs:d.durationMs}))}};
  window.JamMate=api;window.JamAdapter=api;

  function renderStage(){
    const w=$('stage').clientWidth,h=$('stage').clientHeight,compact=w<560;
    const miniW=compact?130:190,miniH=compact?125:175,activeH=compact?248:292;
    const baseActive=Math.min(compact?290:335,w-16),keysW=Math.min(740,w-10);
    const radius=compact?w*.29:w*.32;
    ids.forEach((id,i)=>{
      const a=(i-progress)*2*Math.PI/3,c=Math.cos(a),s=Math.sin(a),front=Math.max(0,c),mix=Math.pow(front,3);
      const width=miniW+((id==='keys'?keysW:baseActive)-miniW)*mix;
      const height=miniH+(activeH-miniH)*mix;
      const orbit=Math.min(1.2,(1-c)/1.5);
      const y=(h-activeH/2-17)+(miniH/2+8-(h-activeH/2-17))*orbit;
      const el=objects[id];el.style.top='0';el.style.width=width+'px';el.style.height=height+'px';
      el.style.setProperty('--device-width',width+'px');el.style.setProperty('--device-height',height+'px');
      el.style.transform='translate(-50%,-50%) translate3d('+(radius*s)+'px,'+y+'px,'+(35-95*(1-c))+'px) rotateY('+(-16*s)+'deg) rotateX('+(6*(1-front))+'deg)';
      el.style.zIndex=String(Math.round(12+c*8));el.style.opacity=String(.52+.48*front);el.style.filter='brightness('+(.76+.24*front)+')';
      el.style.pointerEvents=c<-.75?'none':'auto';
    });
  }
  function animate(t){
    const dt=Math.min(45,t-(frameTime||t));frameTime=t;const gap=focusIndex-progress;
    progress+=gap*(1-Math.exp(-dt/125));
    if(Math.abs(gap)<.0003){progress=focusIndex;frameId=0;frameTime=0;renderStage();return}
    renderStage();frameId=requestAnimationFrame(animate);
  }
  function moveTo(index,chooseTarget=true){
    if(!Number.isFinite(index))return;
    releaseScreenNotes();focusIndex=Math.round(index);
    for(const id of ids){
      const active=id===focused(),el=objects[id];el.classList.toggle('active',active);el.tabIndex=active?-1:0;el.setAttribute('role',active?'group':'button');el.setAttribute('aria-label',active?names[id]:'选择'+names[id]);
      el.querySelectorAll('button,input,select,[role=slider]').forEach(n=>{n.tabIndex=active?0:-1});
    }
    $('instrumentName').textContent=names[focused()];$('instrumentName').setAttribute('aria-label','对'+names[focused()]+'输入');
    if(chooseTarget)setTarget(focused());
    if(reduced.matches){cancelAnimationFrame(frameId);frameId=0;progress=focusIndex;renderStage()}else if(!frameId){frameTime=0;frameId=requestAnimationFrame(animate)}
    log('instrument_focus',{instrument:focused(),target:commandTarget});
  }
  function nearest(id){let delta=mod(ids.indexOf(id)-mod(focusIndex));if(delta>1)delta-=3;return focusIndex+delta}
  function flashPad(index){const p=objects.drums.querySelector('[data-pad="'+index+'"]');if(p){p.classList.add('hit');setTimeout(()=>p.classList.remove('hit'),110)}}
  function updateHarmony(){
    if(!$('harmonyKey'))return;
    const text=v=>typeof v==='string'&&v.trim()?v:'';
    $('harmonyKey').textContent=online()?text(state.key):'';
    $('harmonyChord').textContent=online()?text(state.chord):'';
    const seq=$('harmonySequence');seq.replaceChildren();
    const p=online()?state.progression:null;
    if(Array.isArray(p)&&p.length){p.forEach((c,i)=>{if(i)seq.append(create('span','arrow','→'));const el=create('span',Number.isInteger(state.progressionIndex)&&i===state.progressionIndex?'current':'',text(c));seq.append(el)})}
    else seq.textContent=text(p);
    const hasHarmony=Boolean(text(state.key)||text(state.chord)||seq.textContent);
    objects.harmony.classList.toggle('harmony-empty',!hasHarmony);
    $('harmonySource').textContent=!online()?'尚未连接乐队':hasHarmony?'':'等待和声素材';
  }
  const mixPending=new Map();let mixerSignature='';
  const validGain=v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=1;
  function backendTracks(){
    const raw=state.tracks??state.layers;let tracks=Array.isArray(raw)?raw.map(t=>typeof t==='string'?{id:t}:t).filter(t=>t&&typeof t==='object'):raw&&typeof raw==='object'?Object.entries(raw).map(([id,t])=>t&&typeof t==='object'?{id,...t}:{id}):[];
    tracks=tracks.map(t=>({...t,id:t.id??t.track??t.name})).filter(t=>typeof t.id==='string'&&t.id!=='band');
    if(state.volumes&&typeof state.volumes==='object')for(const id of Object.keys(state.volumes))if(id!=='band'&&!tracks.some(t=>t.id===id))tracks.push({id});
    return tracks.map(t=>({...t,volume:validGain(state.volumes?.[t.id])?state.volumes[t.id]:validGain(t.volume)?t.volume:undefined}));
  }
  function canMix(id){return !online()||id==='band'||(capabilities.independentMix===true&&backendTracks().some(t=>t.id===id&&validGain(t.volume)))}
  function confirmedGain(id){return id==='band'?state.volumes?.band:backendTracks().find(t=>t.id===id)?.volume}
  function updateMixControls(){
    document.querySelectorAll('#stage [data-volume],#stage [data-mute]').forEach(el=>{const id=el.dataset.volume??el.dataset.mute,v=volumes[id];el.title='仅调整本地琴键或鼓垫试听';if(validGain(v)){if(v>0)lastVolumes[id]=v;if(el.dataset.volume)el.value=String(v);else{el.setAttribute('aria-pressed',String(v===0));el.textContent=v===0?'取消静音':'静音'}}});
  }
  function renderMixer(){
    if(!$('mixerExtension')||$('mixerExtension').hidden)return;
    const tracks=online()?backendTracks():['drums','keys'].map(id=>({id,volume:volumes[id]}));
    const signature=JSON.stringify([online(),capabilities.independentMix,tracks.map(t=>[t.id,t.name,t.volume,t.muted,t.enabled,t.status,mixPending.has(t.id)])]);
    if(signature===mixerSignature)return;mixerSignature=signature;
    const root=$('mixerRows');root.replaceChildren();
    $('mixerNote').textContent=!online()?'本地试听音量':capabilities.independentMix===true?'独立声部 · 以实际回执为准':'当前输出未声明独立音轨；请用指令调整编排。';
    if(!tracks.length){root.append(create('p','mixer-note','等待声部状态'));return}
    for(const t of tracks){const row=create('div','mixer-channel');const target=create('button','',names[t.id]||t.name||t.id);target.type='button';target.title='对'+target.textContent+'输入';target.onclick=()=>{if(names[t.id])setTarget(t.id);else{setTarget('band');$('txt').value=($('txt').value?$('txt').value+' ':'')+'对'+(t.name||t.id)+'：';syncComposer();$('txt').focus()}};row.append(target);
      const pending=mixPending.has(t.id);
      if(canMix(t.id)&&validGain(t.volume)){const r=create('input','');r.type='range';r.min='0';r.max='1';r.step='.01';r.value=String(t.volume);r.setAttribute('aria-label',target.textContent+'音量');r.disabled=pending;const out=create('output','',pending?'待确认':Math.round(t.volume*100)+'%');r.oninput=()=>{out.textContent=Math.round(+r.value*100)+'%';if(!online())audio.setVolume(t.id,+r.value)};r.onchange=()=>{setVolume(t.id,+r.value,true);renderMixer()};row.append(r,out)}
      else row.append(create('span','mix-state',t.status|| (t.muted===true?'静音':t.enabled===false?'关闭':t.enabled===true?'启用':'—')));
      root.append(row);
    }
  }
  function openMixer(){closeTempo(false);$('mixerExtension').hidden=false;$('deckArea').classList.add('tempo-open');$('mixerBtn').setAttribute('aria-expanded','true');mixerSignature='';renderMixer();log('mixer_open',{})}
  function closeMixer(restore=true){if($('mixerExtension').hidden)return;$('mixerExtension').hidden=true;$('deckArea').classList.remove('tempo-open');$('mixerBtn').setAttribute('aria-expanded','false');if(restore)$('mixerBtn').focus();log('mixer_close',{})}
  function setVolume(id,value,commit=false){
    const v=Math.max(0,Math.min(1,Number(value)));if(!Number.isFinite(v))return;
    if(online()&&commit&&id!=='band'){
      if(!canMix(id)||mixPending.has(id)){updateMixControls();renderMixer();return}
      const requestId=uid();mixPending.set(id,{requestId,value:v});updateMixControls();renderMixer();
      log('volume',{target:id,value:v,source:'requested'});
      dispatch({type:'mix.volume',requestId,target:id,value:v}).then(ok=>{if(!ok&&mixPending.get(id)?.requestId===requestId){mixPending.delete(id);updateMixControls();renderMixer()}});return;
    }
    if(id in volumes){volumes[id]=v;audio.setVolume(id,v)}
    if(v>0)lastVolumes[id]=v;
    const knob=id==='band'?$('masterKnob'):null;
    if(knob){knob.style.setProperty('--rotation',(-135+v*270)+'deg');knob.setAttribute('aria-valuenow',String(Math.round(v*100)));knob.setAttribute('aria-valuetext',Math.round(v*100)+'%');const valueEl=knob.querySelector('.master-value,.bass-value');if(valueEl)valueEl.textContent=Math.round(v*100)+'%'}
    document.querySelectorAll('[data-volume="'+id+'"]').forEach(el=>el.value=String(v));
    document.querySelectorAll('[data-mute="'+id+'"]').forEach(el=>{el.setAttribute('aria-pressed',String(v===0));el.textContent=v===0?'取消静音':'静音'});
    if(commit){log('volume',{target:id,value:v,source:online()?'requested':'local-preview'});if(online())dispatch({type:'mix.volume',target:id,value:v})}
    if(commit||!online())renderMixer();
  }
  function bindKnob(el,id){
    let drag=null;
    el.addEventListener('pointerdown',e=>{if(e.button>0)return;e.preventDefault();drag={pointer:e.pointerId,y:e.clientY,value:volumes[id]};el.setPointerCapture(e.pointerId);el.classList.add('dragging')});
    el.addEventListener('pointermove',e=>{if(drag?.pointer===e.pointerId)setVolume(id,drag.value+(drag.y-e.clientY)/190)});
    const end=e=>{if(drag?.pointer===e.pointerId){drag=null;el.classList.remove('dragging');setVolume(id,volumes[id],true)}};
    el.addEventListener('pointerup',end);el.addEventListener('pointercancel',end);el.addEventListener('lostpointercapture',end);
    el.addEventListener('keydown',e=>{let v=volumes[id];if(['ArrowUp','ArrowRight'].includes(e.key))v+=.02;else if(['ArrowDown','ArrowLeft'].includes(e.key))v-=.02;else if(e.key==='Home')v=0;else if(e.key==='End')v=1;else return;e.preventDefault();e.stopPropagation();setVolume(id,v,true)});
  }
  function channelMarkup(id){return'<div class="channel-strip"><span class="channel-label">音量</span><input class="volume-fader" data-volume="'+id+'" type="range" min="0" max="1" step=".01" value=".8" aria-label="'+names[id]+'音量"><button type="button" class="mute-button" data-mute="'+id+'" aria-pressed="false">静音</button></div>'}
  function buildStage(){
    for(const id of ids){
      const el=create('div','instrument '+id+' device-case');el.dataset.object=id;objects[id]=el;$('stage').append(el);
      el.addEventListener('pointerdown',e=>{if(focused()!==id){e.preventDefault();e.stopPropagation();moveTo(nearest(id))}},true);
      el.addEventListener('click',e=>{if(focused()===id&&!e.target.closest('button,input,select,[role=slider]'))setTarget(id)});
      el.addEventListener('keydown',e=>{if(e.target===el&&(e.key==='Enter'||e.key===' ')){e.preventDefault();moveTo(nearest(id))}});
    }
    objects.keys.innerHTML='<div class="instrument-head"><div class="key-left"><button type="button" class="device-control" id="melodyRecord" aria-pressed="false"><span class="record-dot"></span><span id="melodyRecordLabel">录入旋律</span></button><button type="button" class="device-control" id="midiConnect">连接 MIDI</button></div><div class="key-right"><span id="melodyCount"></span><button type="button" class="device-control" id="melodySend" hidden>加入输入</button><div class="octave-controls"><button type="button" class="device-control" id="octaveDown" aria-label="降低八度">−</button><span class="octave-label">C4</span><button type="button" class="device-control" id="octaveUp" aria-label="提高八度">+</button></div></div></div><div class="instrument-body">'+channelMarkup('keys')+'<div class="keyboard"></div></div>';
    objects.drums.innerHTML='<div class="instrument-head"><label>节奏组</label><div class="meter-selector" role="group" aria-label="拍号">'+['4/4','3/4','6/8'].map(m=>'<button type="button" data-meter="'+m+'" aria-pressed="'+(m===localMeter)+'" aria-label="'+m+' 拍">'+m+'</button>').join('')+'</div></div><div class="instrument-body">'+channelMarkup('drums')+'<div class="pad-grid">'+Array.from({length:16},(_,i)=>'<button class="pad" type="button" data-pad="'+i+'" aria-label="'+['底鼓','军鼓','踩镲','打击乐'][i%4]+' '+(Math.floor(i/4)+1)+'"><span>'+(['KICK','SNARE','HAT','PERC'][i%4])+'</span></button>').join('')+'</div></div>';
    objects.harmony.innerHTML='<div class="harmony-display" aria-label="和声状态"><div class="harmony-top"><span>调性</span><span class="harmony-key" id="harmonyKey"></span></div><div class="harmony-current"><span>当前和弦</span><strong class="harmony-chord" id="harmonyChord"></strong></div><div class="harmony-progression"><span>和弦进行</span><div class="harmony-sequence" id="harmonySequence"></div></div><span id="harmonySource" class="harmony-source">尚未连接乐队</span></div>';
    for(const el of document.querySelectorAll('[data-volume]')){el.addEventListener('input',()=>{setVolume(el.dataset.volume,+el.value)});el.addEventListener('change',()=>setVolume(el.dataset.volume,+el.value))}
    for(const el of document.querySelectorAll('[data-mute]'))el.onclick=()=>{const id=el.dataset.mute,v=volumes[id];if(validGain(v))setVolume(id,v===0?(lastVolumes[id]||.8):0)};
    document.querySelectorAll('[data-meter]').forEach(b=>b.onclick=()=>applyMeter(b.dataset.meter));
    for(const pad of objects.drums.querySelectorAll('.pad')){
      const hit=async()=>{if(focused()!=='drums')return;try{await audio.ensure();const index=+pad.dataset.pad;audio.drum(index%4);flashPad(index);log('pad_hit',{pad:index,sound:['kick','snare','hat','perc'][index%4],source:'local-preview'})}catch(error){toast('浏览器音频不可用')}};
      pad.addEventListener('pointerdown',e=>{if(e.button>0)return;e.preventDefault();hit()});pad.addEventListener('click',e=>{if(e.detail===0)hit()});
    }
    bindKnob($('masterKnob'),'band');
    $('melodyRecord').onclick=toggleMelody;$('melodySend').onclick=addCapturedMelody;$('midiConnect').onclick=connectMidi;
    $('octaveDown').onclick=()=>changeOctave(-1);$('octaveUp').onclick=()=>changeOctave(1);
    whiteCount=$('stage').clientWidth<560?7:14;keyboardMarkup();
    for(const id of Object.keys(volumes))setVolume(id,volumes[id]);
    moveTo(1,false);renderStage();
  }
  function recordNote(kind,note,velocity,source){
    if(!melodyCapture)return;const key=source+':'+note;if(kind==='note_off'&&!melodyDown.has(key))return;
    if(melodyEvents.length>=10000){finishMelody();toast('本段旋律已达到记录上限');return}
    const hardwareChannel=source.startsWith('midi:')?Number(source.split(':').at(-1))+1:1;
    const event={kind,note,velocity,timestampMs:Math.round(performance.now()-melodyStart),source,channel:hardwareChannel,track:0};
    melodyEvents.push(event);if(kind==='note_on')melodyDown.set(key,event);else melodyDown.delete(key);updateMelody();
  }
  function keyVisual(note){const on=fileNotes.has(note)||[...sounding.values()].some(v=>v.note===note);objects.keys.querySelectorAll('[data-note="'+note+'"]').forEach(b=>b.classList.toggle('pressed',on))}
  async function noteOn(note,velocity=90,source='screen'){
    const key=source+':'+note;if(sounding.has(key))noteOff(note,source);
    const voice={note,source,token:null,ended:false};sounding.set(key,voice);keyVisual(note);
    if(!source.startsWith('bass'))recordNote('note_on',note,velocity,source);
    log('note_on',{midi:note,velocity,source});
    try{await audio.ensure();if(!voice.ended)voice.token=audio.noteOn(note,velocity,source)}catch(error){log('audio_error',{message:String(error.message||error)},{level:'error'});toast('浏览器音频不可用')}
  }
  function noteOff(note,source='screen'){
    const key=source+':'+note,v=sounding.get(key);if(!v)return;
    v.ended=true;if(v.token)audio.noteOff(v.token);sounding.delete(key);keyVisual(note);
    if(!source.startsWith('bass'))recordNote('note_off',note,0,source);
    log('note_off',{midi:note,source});
  }
  function releaseScreenNotes(){for(const v of [...sounding.values()])if(!v.source.startsWith('midi:'))noteOff(v.note,v.source);notePointers.clear();typingHeld.clear()}
  function releaseAllNotes(){for(const v of [...sounding.values()])noteOff(v.note,v.source);notePointers.clear();typingHeld.clear();audio.releaseAll()}
  function keyboardMarkup(){
    const kb=objects.keys.querySelector('.keyboard');kb.replaceChildren();kb.style.setProperty('--whites',whiteCount);
    const white=[0,2,4,5,7,9,11],base=(octave+1)*12;
    const bind=(b,n)=>{
      b.dataset.note=n;b.setAttribute('aria-label',['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][n%12]+(Math.floor(n/12)-1));b.tabIndex=focused()==='keys'?0:-1;
      b.addEventListener('pointerdown',e=>{if(focused()!=='keys'||e.button>0)return;e.preventDefault();e.stopPropagation();b.setPointerCapture(e.pointerId);const source='screen:'+e.pointerId;notePointers.set(e.pointerId,{note:n,source});noteOn(n,90,source)});
      const up=e=>{const p=notePointers.get(e.pointerId);if(p){noteOff(p.note,p.source);notePointers.delete(e.pointerId)}};
      b.addEventListener('pointerup',up);b.addEventListener('pointercancel',up);b.addEventListener('lostpointercapture',up);
      b.addEventListener('keydown',e=>{if(focused()==='keys'&&(e.key===' '||e.key==='Enter')&&!e.repeat){e.preventDefault();e.stopPropagation();noteOn(n,90,'accessibility')}});
      b.addEventListener('keyup',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();noteOff(n,'accessibility')}});
      b.addEventListener('blur',()=>noteOff(n,'accessibility'));
    };
    for(let i=0;i<whiteCount;i++){const n=base+white[i%7]+12*Math.floor(i/7),b=create('button','white-key');b.type='button';if(n%12===0)b.append(create('span','', 'C'+(Math.floor(n/12)-1)));bind(b,n);kb.append(b)}
    for(let i=0;i<whiteCount-1;i++)if([0,1,3,4,5].includes(i%7)){const b=create('button','black-key');b.type='button';b.style.left='calc('+((i+1)*100/whiteCount)+'% - 100% / '+whiteCount+' * .29)';bind(b,base+white[i%7]+12*Math.floor(i/7)+1);kb.append(b)}
    objects.keys.querySelector('.octave-label').textContent='C'+octave;objects.keys.querySelector('.octave-controls').hidden=whiteCount>7;
    for(const v of sounding.values())keyVisual(v.note);
  }
  function changeOctave(d){const n=Math.max(1,Math.min(7,octave+d));if(n!==octave){releaseScreenNotes();octave=n;keyboardMarkup();log('keyboard_octave',{octave})}}
  function updateMelody(){
    const count=melodyEvents.filter(e=>e.kind==='note_on').length;
    $('melodyRecord').classList.toggle('recording',melodyCapture);$('melodyRecord').setAttribute('aria-pressed',String(melodyCapture));
    $('melodyRecordLabel').textContent=melodyCapture?'完成':count?'重新录入':'录入旋律';$('melodyCount').textContent=count?count+' 音':'';
    $('melodySend').hidden=melodyCapture||!count;
  }
  function finishMelody(){
    if(!melodyCapture)return;
    for(const e of melodyDown.values())melodyEvents.push({...e,kind:'note_off',velocity:0,timestampMs:Math.round(performance.now()-melodyStart)});
    melodyDown.clear();melodyCapture=false;log('midi_capture_stop',{events:melodyEvents.length,notes:melodyEvents.filter(e=>e.kind==='note_on').length});updateMelody();
  }
  function toggleMelody(){
    if(melodyCapture){finishMelody();return}
    releaseScreenNotes();melodyEvents=[];melodyDown.clear();melodyStart=performance.now();melodyCapture=true;updateMelody();log('midi_capture_start',{target:'keys'});
  }
  function addCapturedMelody(){
    if(!melodyEvents.length||melodyCapture)return;
    if(drafts.length>=(micSession||micPending?3:4)){toast('一次最多添加 4 份素材，录音会保留一个位置');return}
    const events=melodyEvents.map(e=>({...e})),count=events.filter(e=>e.kind==='note_on').length;
    addDraft({kind:'midi',origin:'keyboard',name:'键盘旋律',events,tracks:[{index:0,name:'屏幕 / MIDI 键盘'}],durationMs:Math.max(...events.map(e=>e.timestampMs)),size:0,summary:count+' 个音 · 已记录真实时值',target:'keys',mode:'humming',tempos:[],tempoProvided:false});
    melodyEvents=[];updateMelody();hint('旋律已加入输入，可试听或补充一句要求',5000);
  }
  function attachMidi(){
    if(!midiAccess)return;midiDevices.clear();
    for(const input of midiAccess.inputs.values()){
      if(input.state!=='connected'){input.onmidimessage=null;continue}midiDevices.add(input.id);
      input.onmidimessage=e=>{const [status,n,v=0]=e.data,type=status&240,source='midi:'+input.id+':'+(status&15);
        if(type===144&&v>0)noteOn(n,v,source);else if(type===128||(type===144&&v===0))noteOff(n,source);
        else if(type===176&&(n===120||n===123))for(const voice of [...sounding.values()])if(voice.source===source)noteOff(voice.note,voice.source);
      };
    }
    for(const v of [...sounding.values()])if(v.source.startsWith('midi:')&&![...midiDevices].some(id=>v.source.startsWith('midi:'+id+':')))noteOff(v.note,v.source);
    $('midiConnect').textContent=midiDevices.size?'MIDI 已连接':'连接 MIDI';
    log('midi_devices',{inputs:[...midiAccess.inputs.values()].map(x=>({id:x.id,name:x.name,state:x.state}))});
  }
  async function connectMidi(){
    if(!navigator.requestMIDIAccess){toast('此浏览器不支持 MIDI，可继续使用屏幕琴键');log('midi_unavailable',{});return}
    $('midiConnect').disabled=true;
    try{await audio.ensure();midiAccess=await navigator.requestMIDIAccess({sysex:false});midiAccess.onstatechange=attachMidi;attachMidi();if(!midiDevices.size)toast('连接实体 MIDI 键盘后会自动识别')}
    catch(error){toast('MIDI 访问失败，请检查权限和设备');log('midi_error',{message:error.message},{level:'error'})}
    finally{$('midiConnect').disabled=false}
  }
  async function togglePlayback(){
    if(transportPending)return;
    if(online()){
      const p=phase(),action=p==='playing'?'pause':p==='paused'?'resume':'start';
      if(action!=='start'&&capabilities.pause!==true){toast('当前接入层尚未提供暂停与继续能力');return}
      const requestId=uid();transportPending={requestId,action};updateTransport();
      const ok=await dispatch({type:'transport.'+action,requestId,...(action==='start'?{config:{bpm:localBpm,meter:localMeter,tempoUnit:tempoUnit(localMeter)}}:{})});
      if(!ok&&transportPending?.requestId===requestId){transportPending=null;updateTransport()}return;
    }
    if(micSession){toast('正在录音，结束后再开始本地节拍');return}
    try{
      await audio.ensure();
      if(audio.getPreviewState().playing)audio.pausePreview();
      else{stopMidiPreview();audio.startPreview(localBpm,{meter:localMeter,onBeat:e=>{for(const sound of e.sounds||[]){const index=['kick','snare','hat','perc'].indexOf(typeof sound==='string'?sound:sound.sound);if(index>=0)flashPad(index)}}});previewStarted=true}
      updateTransport();log('transport',{playing:audio.getPreviewState().playing,clock:'local-preview',bpm:localBpm,meter:localMeter,tempoUnit:tempoUnit(localMeter)});
    }catch(error){toast('无法启动浏览器音频');log('audio_error',{message:error.message},{level:'error'})}
  }
  function openTempo(){
    closeMixer(false);
    $('bpmInput').value=tempoPending?.bpm??(online()&&bpmValid(state.bpm)?state.bpm:localBpm);
    $('tempoNote').textContent=online()?'提交后等待乐队确认':'30–250 BPM · 调整本地试听速度';
    $('tempoPopover').hidden=false;$('deckArea').classList.add('tempo-open');$('tempoBtn').setAttribute('aria-expanded','true');taps=[];$('bpmInput').focus();$('bpmInput').select();
  }
  function closeTempo(restore=true){$('tempoPopover').hidden=true;$('deckArea').classList.remove('tempo-open');$('tempoBtn').setAttribute('aria-expanded','false');if(restore)$('tempoBtn').focus()}
  async function applyTempo(e){
    e.preventDefault();const v=Number($('bpmInput').value);
    if(!$('bpmInput').value||!bpmValid(v)||!Number.isInteger(v)){$('tempoNote').textContent='请输入 30–250 之间的整数';$('bpmInput').focus();return}
    closeTempo();clearTimeout(wheelTempoTimer);wheelTempoDraft=null;
    await commitTempo(v);
  }
  async function commitTempo(v){
    if(online()){
      if(tempoPending){wheelTempoDraft=v;return}
      if(capabilities.tempo===false){toast('当前后端暂不支持调整速度');return}
      const requestId=uid();tempoPending={requestId,bpm:v};updateTransport();const ok=await dispatch({type:'tempo.set',requestId,bpm:v,tempoUnit:tempoUnit(meterValid(state.meter)?state.meter:localMeter)});
      if(!ok&&tempoPending?.requestId===requestId){tempoPending=null;updateTransport()}
    }else{if(localBpm===v)return;localBpm=v;audio.setTempo(v);updateTransport();log('tempo_changed',{bpm:v,meter:localMeter,tempoUnit:tempoUnit(localMeter),source:'local-preview'})}
  }
  function flushWheelTempo(){if(wheelTempoDraft===null||tempoPending||!online())return;const v=wheelTempoDraft;wheelTempoDraft=null;clearTimeout(wheelTempoTimer);if(v!==state.bpm)commitTempo(v);else updateTransport()}
  function wheelTempo(e){if(Math.abs(e.deltaY)<Math.abs(e.deltaX)||!e.deltaY)return;e.preventDefault();const current=wheelTempoDraft??tempoPending?.bpm??(online()&&bpmValid(state.bpm)?state.bpm:localBpm);const v=Math.max(30,Math.min(250,Math.round(current)+(e.deltaY<0?1:-1)*(e.shiftKey?5:1)));if(v===current)return;if(!$('tempoPopover').hidden)$('bpmInput').value=v;if(online()){if(capabilities.tempo===false){toast('当前后端暂不支持调整速度');return}wheelTempoDraft=v;updateTransport();clearTimeout(wheelTempoTimer);wheelTempoTimer=setTimeout(flushWheelTempo,160)}else commitTempo(v)}
  function adjustTempo(delta){const n=Number($('bpmInput').value);$('bpmInput').value=Math.max(30,Math.min(250,(Number.isFinite(n)?n:localBpm)+delta))}
  function tapTempo(){
    const now=performance.now();if(now-(taps.at(-1)||0)>2400)taps=[];taps.push(now);if(taps.length>7)taps.shift();
    if(taps.length<2){$('tempoNote').textContent='继续按节拍点击';return}
    const avg=(taps.at(-1)-taps[0])/(taps.length-1),value=Math.round(60000/avg);
    if(bpmValid(value)){$('bpmInput').value=value;$('tempoNote').textContent=taps.length+' 次点拍 · 点击应用'}
  }

  function seconds(ms){const s=Math.max(0,Math.round(ms/1000));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
  function addDraft(raw){
    const draft={...raw,id:uid(),target:raw.target||commandTarget,mode:raw.mode||inputMode,trackFilter:'all'};
    if(draft.kind==='audio')draft.url=URL.createObjectURL(draft.blob||draft.file);
    drafts.push(draft);selectedDraftId=draft.id;renderDrafts();syncComposer();return draft;
  }
  function removeDraft(id){
    const d=drafts.find(x=>x.id===id);if(!d)return;
    if(previewDraftId===id)stopMidiPreview();if(d.url)URL.revokeObjectURL(d.url);
    drafts=drafts.filter(x=>x.id!==id);if(selectedDraftId===id)selectedDraftId=drafts.at(-1)?.id||null;
    renderDrafts();syncComposer();log('draft_removed',{id,kind:d.kind,name:d.name});
  }
  function selectedEvents(d){return d.trackFilter==='all'?d.events:d.events.filter(e=>e.track===Number(d.trackFilter))}
  function stopMidiPreview(){
    previewGeneration++;audio.stopMidiPreview();previewDraftId=null;
    const notes=[...fileNotes];fileNotes.clear();notes.forEach(keyVisual);
    document.querySelectorAll('[data-preview-midi]').forEach(b=>b.textContent='试听');
  }
  async function previewMidi(d){
    if(previewDraftId===d.id){stopMidiPreview();return}
    if(micSession){toast('请先结束录音');return}
    stopMidiPreview();audio.pausePreview();updateTransport();previewDraftId=d.id;const generation=previewGeneration;
    try{
      const result=await audio.startMidiPreview(selectedEvents(d),{
        onNote:e=>{if(generation!==previewGeneration)return;if(e.kind==='note_on'&&e.velocity>0)fileNotes.add(e.note);else fileNotes.delete(e.note);keyVisual(e.note)},
        onEnded:()=>{if(generation===previewGeneration&&previewDraftId===d.id){previewDraftId=null;const notes=[...fileNotes];fileNotes.clear();notes.forEach(keyVisual);const b=document.querySelector('[data-preview-midi="'+d.id+'"]');if(b)b.textContent='试听'}}
      });
      if(generation!==previewGeneration||previewDraftId!==d.id||!result.playing)return;
      const b=document.querySelector('[data-preview-midi="'+d.id+'"]');if(b)b.textContent='停止试听';
      if(result.truncated)toast('本地试听前 2 分钟，提交时保留完整文件');
      log('midi_preview',{name:d.name,events:selectedEvents(d).length,source:'local-midi-preview',truncated:result.truncated});
    }catch(error){if(generation!==previewGeneration)return;previewDraftId=null;toast(String(error.message||error));log('audio_error',{message:String(error.message||error)},{level:'error'})}
  }
  function targetSelect(d){
    const select=create('select','draft-target');select.setAttribute('aria-label',d.name+'的作用对象');
    for(const [id,label]of Object.entries(names)){const option=create('option','',label);option.value=id;select.append(option)}
    select.value=d.target;select.onchange=()=>{d.target=select.value;log('draft_target',{id:d.id,target:d.target})};return select;
  }
  function renderDrafts(){
    const root=$('drafts');
    for(const el of root.querySelectorAll('audio'))el.pause();
    root.replaceChildren();if(!drafts.length)return;
    const list=create('div','draft-tabs');root.append(list);
    for(const d of drafts){
      const chip=create('button','draft-tab'+(selectedDraftId===d.id?' selected':''),d.name);chip.type='button';chip.setAttribute('aria-pressed',String(selectedDraftId===d.id));chip.onclick=()=>{selectedDraftId=d.id;renderDrafts()};list.append(chip);
    }
    const d=drafts.find(x=>x.id===selectedDraftId)||drafts[0];if(!d)return;
    const card=create('article','draft draft-card'),head=create('div','draft-heading'),title=create('strong','draft-name',d.name),close=create('button','draft-remove');
    close.type='button';close.innerHTML=icon('close');close.setAttribute('aria-label','移除 '+d.name);close.onclick=()=>removeDraft(d.id);head.append(title,close);card.append(head);
    card.append(create('p','draft-meta',d.summary||((d.kind==='audio'?'音频':'素材')+' · '+imports.formatBytes(d.size||0))));
    if(d.kind==='audio'){
      const player=create('audio');player.controls=true;player.preload='metadata';player.src=d.url;player.setAttribute('aria-label','试听 '+d.name);
      player.addEventListener('loadedmetadata',()=>{if(Number.isFinite(player.duration)&&!d.durationMs){d.durationMs=Math.round(player.duration*1000);card.querySelector('.draft-meta').textContent=seconds(d.durationMs)+' · '+imports.formatBytes(d.size)}});
      player.addEventListener('play',()=>{if(micSession){player.pause();toast('请先结束录音');return}stopMidiPreview();audio.pausePreview();updateTransport();log('audio_preview',{name:d.name,source:'user-file'})});
      player.addEventListener('error',()=>{const line=create('p','draft-meta','此浏览器无法试听该编码；原始文件仍已保留。');card.append(line)},{once:true});card.append(player);
    }else if(d.kind==='text'){
      const text=create('pre','draft-text',d.text.length>1200?d.text.slice(0,1200)+'\n…（提交时包含完整文本）':d.text);card.append(text);
    }else if(d.kind==='midi'){
      const row=create('div','draft-midi-row'),button=create('button','',previewDraftId===d.id?'停止试听':'试听');button.type='button';button.dataset.previewMidi=d.id;button.onclick=()=>previewMidi(d);row.append(button);
      row.append(create('span','draft-meta','本地合成音 · '+seconds(d.durationMs||0)));
      if((d.tracks||[]).length>1){
        const select=create('select');select.setAttribute('aria-label','MIDI 轨道');const all=create('option','','所有轨道');all.value='all';select.append(all);
        d.tracks.forEach((t,i)=>{const option=create('option','',t.name||'轨道 '+(i+1));option.value=String(t.index??i);select.append(option)});select.value=d.trackFilter;select.onchange=()=>{if(previewDraftId===d.id)stopMidiPreview();d.trackFilter=select.value;log('midi_track',{id:d.id,track:d.trackFilter})};row.append(select);
      }
      card.append(row);
      if(d.tempos?.some(t=>t.provided))card.append(create('p','draft-meta','文件速度 '+Math.round(d.tempos.find(t=>t.provided).bpm)+' BPM · 不改变会话速度'));
    }
    const controls=create('div','draft-routing');
    if(d.kind==='text')controls.append(create('span','draft-text-target','文字提示 · 跟随输入对象：'+names[commandTarget]));
    else controls.append(create('span','','给'),targetSelect(d));
    if(d.kind==='audio'){
      const select=create('select');select.setAttribute('aria-label',d.name+'的用途');
      for(const [id,label]of Object.entries(modes)){const option=create('option','',label);option.value=id;select.append(option)}select.value=d.mode;select.onchange=()=>{d.mode=select.value;log('draft_mode',{id:d.id,mode:d.mode})};controls.append(select);
    }
    controls.append(create('span','draft-status',sending?'正在提交':'待提交'));card.append(controls);root.append(card);root.querySelectorAll('select').forEach(el=>el.disabled=sending);
  }
  async function importFiles(files){
    if(importing||!files.length)return;if(composerRecording()){toast('请先结束录音，再添加文件');return}importing=true;$('attachBtn').disabled=true;syncComposer();
    const targetAtImport=commandTarget,modeAtImport=inputMode;
    try{
      for(const file of files){
        if(drafts.length>=4){toast('一次最多添加 4 份素材');break}
        try{
          const d=await imports.parseFile(file);addDraft({...d,origin:'file',target:targetAtImport,mode:modeAtImport});
          log('file_imported',{name:d.name,kind:d.kind,size:d.size,summary:d.summary,...(d.kind==='midi'?{format:d.format,ppq:d.ppq,tempoProvided:d.tempoProvided,notes:d.events.filter(e=>e.kind==='note_on').length,tracks:d.tracks}:{} )});
        }catch(error){toast(String(error.message||error));log('file_error',{name:file.name,message:String(error.message||error)},{level:'error'})}
      }
      if(drafts.length)hint('素材已加入输入，可试听或补充一句要求',5000);
    }finally{importing=false;$('attachBtn').disabled=false;$('fileInput').value='';syncComposer()}
  }
  function composerRecording(){return Boolean(micSession&&!micSession.wake)||micPending&&!autoWakePending}
  function syncComposer(){const enabled=!!($('txt').value.trim()||drafts.length), recording=composerRecording();$('sendText').disabled=!enabled||sending||importing||recording;$('sendText').setAttribute('aria-label',sending?'正在提交':'发送输入');$('sendText').title='发送输入';$('attachBtn').disabled=importing||recording;$('drafts').querySelectorAll('select').forEach(el=>el.disabled=sending)}
  function mediaPayload(d){
    const base={target:d.target,fileName:d.name,origin:d.origin};
    if(d.kind==='audio')return{...base,type:'input.audio',mode:d.mode,audio:d.blob||d.file,durationMs:d.durationMs??null};
    return{...base,type:'input.midi',events:selectedEvents(d).map(e=>({...e})),tempos:d.tempos||[],tempoProvided:!!d.tempoProvided,ppq:d.ppq??null,tracks:d.tracks||[],sourceBpm:d.tempoProvided?d.tempos?.find(t=>t.provided)?.bpm??null:null,...(d.file?{file:d.file}:{})};
  }
  async function sendInput(e){
    e.preventDefault();if(sending||importing||composerRecording())return;
    const content=$('txt').value,text=content.trim(),saved=drafts.slice();if(!text&&!saved.length)return;
    const docs=saved.filter(d=>d.kind==='text').map(d=>({name:d.name,text:d.text,role:'user_context',target:commandTarget}));
    const media=saved.filter(d=>d.kind!=='text');
    if(online()&&media.length>1&&capabilities.batchInput!==true){toast('当前接入层不支持多份音乐素材，请逐份提交');return}
    const instruction=[text,...docs.map(d=>'附件 '+d.name+'\n'+d.text)].filter(Boolean).join('\n\n');
    const common={instruction,documents:docs,phase:phase(),contextTarget:commandTarget};
    let action;
    if(!media.length)action={type:'input.text',target:commandTarget,text:instruction,documents:docs,phase:phase()};
    else if(media.length===1)action={...mediaPayload(media[0]),...common};
    else action={type:'input.bundle',target:commandTarget,inputs:media.map(mediaPayload),...common};
    sending=true;const ticket=++sendTicket;syncComposer();const ok=await dispatch(action);if(ticket!==sendTicket)return;sending=false;
    if(ok){
      if($('txt').value===content)$('txt').value='';
      const sentIds=new Set(saved.map(d=>d.id));
      for(const d of saved){if(previewDraftId===d.id)stopMidiPreview();if(d.url)URL.revokeObjectURL(d.url)}
      drafts=drafts.filter(d=>!sentIds.has(d.id));selectedDraftId=drafts.at(-1)?.id||null;renderDrafts();hint('已提交，等待乐队处理回执',6000);
    }else if(!online())toast('尚未连接后端，输入已保留，可继续试听');
    syncComposer();
  }
  function showClarification(event){
    if(typeof event.message!=='string'||!Array.isArray(event.options))return;
    clarification=event;let busy=false;const root=$('clarification');root.replaceChildren();root.hidden=false;root.append(create('span','',event.message));
    for(const option of event.options.slice(0,4)){
      if(typeof option.label!=='string')continue;
      const b=create('button','',option.label);b.type='button';b.onclick=async()=>{if(busy||clarification!==event)return;busy=true;root.querySelectorAll('button').forEach(button=>button.disabled=true);const ok=await dispatch({type:'input.confirm',replyTo:event.requestId,choice:option.id});if(clarification!==event)return;if(ok)clearClarification();else{busy=false;root.querySelectorAll('button').forEach(button=>button.disabled=false)}};root.append(b);
    }
  }
  function clearClarification(){clarification=null;const root=$('clarification');if(root){root.hidden=true;root.replaceChildren()}}

  function updateRecording(){
    const active=!!micSession;
    document.body.classList.toggle('rec',active&&!micSession.wake);
    document.body.classList.toggle('wake-listening',active&&micSession.wake);
    $('mic').disabled=micPending;$('mic').setAttribute('aria-pressed',String(active));$('mic').innerHTML=icon(active?'stop':'mic');
    const speech = inputMode === 'speech';
    const label = active && micSession.wake ? '关闭语音唤醒' :
      micPending ? (active ? '正在保存录音' : '等待麦克风权限') : active ? '停止录音' :
        speech ? '开启语音唤醒' : '开始录音';
    $('mic').setAttribute('aria-label',label);
    $('mic').title=label;
    $('inputModes').querySelectorAll('button').forEach(b=>b.disabled=(active&&!micSession.wake)||micPending);
    $('recordingCaption').hidden=!active;
    if(active){
      $('recordingCaption').textContent=micSession.wake ? micSession.caption :
        '录给'+names[micSession.target]+' · '+modes[micSession.mode]+' · '+seconds(performance.now()-micSession.started);
      $('inputHint').textContent='';
    }
    syncComposer();
  }
  function drawWave(){
    if(!micSession)return;const c=$('wave'),ctx=c.getContext('2d'),s=micSession;s.analyser.getByteTimeDomainData(s.samples);
    ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle='#e9e0d0';ctx.lineWidth=2;ctx.beginPath();
    for(let i=0;i<c.width;i++){const v=s.samples[Math.floor(i/c.width*s.samples.length)]/128-1;ctx[i?'lineTo':'moveTo'](i,c.height/2+v*c.height*.44)}ctx.stroke();waveId=requestAnimationFrame(drawWave);
  }
  function cleanupMic(s){
    s.stream.getTracks().forEach(t=>t.stop());try{s.source.disconnect();s.analyser.disconnect()}catch{}
    if(micSession===s){cancelAnimationFrame(waveId);clearInterval(micTimer);clearTimeout(micLimit);micSession=null;micPending=false;updateRecording();updateHint()}
  }
  function stopRecording(){
    const s=micSession;if(!s||s.recorder.state!=='recording')return;
    s.stopTime=performance.now();micPending=true;updateRecording();
    try{s.recorder.stop()}catch(error){s.failed=true;cleanupMic(s);toast('录音中断，请重试')}
  }
  const waitingForWake = '在听 · Hey JamMate / Hey 节奏组 / Hey 旋律组';
  function closeWakeMic(session) {
    clearTimeout(session.wakeTimer);
    session.close();
    if (micSession === session) {
      cancelAnimationFrame(waveId);
      micSession = null;
      updateRecording();
      updateHint();
      log('mic_stop', {mode: 'wake'});
    }
  }

  async function recognizeWakePhrase(session, phrase) {
    if (micSession !== session) return;
    session.hearing = false;
    if (session.busy) return;
    if (!phrase.samples) {
      if (phrase.tooLong || session.hearingStartedArmed) session.target = null;
      session.caption = phrase.tooLong ? '这句太长了，请重新唤醒并说一句短指令' : waitingForWake;
      updateRecording();
      return;
    }
    const wakeTarget = session.target;
    session.target = null;
    clearTimeout(session.wakeTimer);
    session.busy = true;
    session.caption = '正在识别语音…';
    updateRecording();
    const result = await dispatch({type:'voice.turn', samples:phrase.samples, sampleRate:phrase.sampleRate,
      wakeTarget, current:() => micSession === session});
    if (micSession !== session) return;
    if (result?.target) {
      session.caption = result.target === 'band' ? '我在。' : names[result.target] + '在听。';
      updateRecording();
      const played = await session.acknowledge().catch(() => false);
      if (micSession !== session) return;
      log('voice_wake', {target: result.target, replyPlayed: played});
      session.target = result.target;
      session.caption = (result.target === 'band' ? 'JamMate' : names[result.target]) + '在听，请说指令';
      session.wakeTimer = setTimeout(() => {
        session.target = null;
        if (!session.hearing && !session.busy) {
          session.caption = waitingForWake;
          updateRecording();
        }
      }, 10_000);
    } else {
      session.caption = result?.sent ? '指令已提交 · 继续说唤醒词' : waitingForWake;
    }
    session.busy = false;
    updateRecording();
  }

  async function autoStartWake(initial = false) {
    const speechMode = inputMode === 'speech' || (initial && inputMode === 'auto');
    if (!wakeDisabled && speechMode && capabilities.voiceWake && navigator.mediaDevices?.getUserMedia
        && !micSession && !micPending && !document.hidden) {
      await startWakeMic(true);
    }
  }

  async function startWakeMic(automatic = false) {
    if (!capabilities.voiceWake) { if (!automatic) toast('语音服务尚未配置'); return; }
    if (document.hidden || sending || importing || micSession || micPending) return;
    if (inputMode !== 'speech') setMode('speech');
    micPending = true;
    autoWakePending = automatic;
    updateRecording();
    let capture;
    try {
      const {openWakeInput} = await import('./jam-v4-voice.js');
      const session = {wake:true, target:null, busy:false, hearing:false, caption:waitingForWake};
      capture = await openWakeInput(audio.ensure, phrase => void recognizeWakePhrase(session, phrase), () => {
        if (micSession !== session || session.busy) return;
        session.hearing = true;
        session.hearingStartedArmed = Boolean(session.target);
        if (session.target) clearTimeout(session.wakeTimer);
        session.caption = session.target ? names[session.target] + '在听…' : '在听这一句…';
        updateRecording();
      });
      if (document.hidden || !online()) { capture.close(); return; }
      Object.assign(session, capture, {samples: new Uint8Array(capture.analyser.fftSize)});
      for (const track of capture.stream.getAudioTracks()) track.addEventListener('ended', () => closeWakeMic(session), {once:true});
      micSession = session;
      log('mic_start', {mode:'wake'});
    } catch (error) {
      capture?.close();
      if (error.name === 'NotAllowedError') hint('允许此网页访问麦克风，即可直接语音唤醒');
      else hint('语音监听暂不可用，请检查麦克风或用文字输入');
      log('mic_error', {message:error.message}, {level:'error'});
    } finally {
      micPending = false;
      autoWakePending = false;
      updateRecording();
    }
  }

  async function toggleMic(){
    if(micPending)return;
    if(micSession?.wake){wakeDisabled=true;closeWakeMic(micSession);return}
    if(micSession){stopRecording();return}
    if(inputMode==='speech'){wakeDisabled=false;await startWakeMic();return}
    if(importing){toast('文件正在读取，完成后即可录音');return}
    if(drafts.length>=4){toast('请先提交或移除一份素材');return}
    if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){toast('当前环境无法录音，请用 HTTPS 或 localhost 打开，也可导入音频文件');log('mic_unavailable',{});return}
    const targetAtStart=commandTarget,modeAtStart=inputMode;micPending=true;updateRecording();let stream=null,source=null,analyser=null,started=null;
    try{
      const ctx=await audio.ensure();stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
      if(document.visibilityState==='hidden'){stream.getTracks().forEach(t=>t.stop());micPending=false;updateRecording();return}
      stopMidiPreview();for(const a of $('drafts').querySelectorAll('audio'))a.pause();if(audio.getPreviewState().playing){audio.pausePreview();updateTransport();log('preview_paused_for_capture',{})}
      source=ctx.createMediaStreamSource(stream);analyser=ctx.createAnalyser();analyser.fftSize=512;source.connect(analyser);
      const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
      const recorder=new MediaRecorder(stream,mime?{mimeType:mime}:undefined);
      const s={stream,source,analyser,samples:new Uint8Array(analyser.fftSize),recorder,chunks:[],target:targetAtStart,mode:modeAtStart,started:performance.now(),failed:false,bytes:0};
      micSession=s;started=s;
      recorder.ondataavailable=e=>{if(e.data.size){s.chunks.push(e.data);s.bytes+=e.data.size;if(s.bytes>38*1024*1024&&micSession===s){stopRecording();toast('录音已达到大小上限，已保存为草稿')}}};
      recorder.onerror=e=>{s.failed=true;log('mic_error',{message:String(e.error?.message||'录音失败')},{level:'error'});cleanupMic(s);toast('录音中断，请重试')};
      recorder.onstop=()=>{
        const durationMs=Math.round((s.stopTime||performance.now())-s.started),blob=new Blob(s.chunks,{type:recorder.mimeType||mime||'audio/webm'});cleanupMic(s);if(s.failed)return;
        log('mic_stop',{durationMs,bytes:blob.size,mime:blob.type,target:s.target,mode:s.mode});
        if(!blob.size){toast('没有收到录音数据');return}
        if(drafts.length>=4){toast('输入素材已满，无法保留新录音');return}
        addDraft({kind:'audio',origin:'microphone',name:'录音 · '+names[s.target],blob,mime:blob.type,size:blob.size,durationMs,target:s.target,mode:s.mode,summary:seconds(durationMs)+' · '+modes[s.mode]+' · 真实录音'});
      };
      recorder.start(250);micPending=false;updateRecording();drawWave();micTimer=setInterval(updateRecording,500);
      const limit=online()?({auto:20,humming:20,rhythm:18}[s.mode]||30):300;
      micLimit=setTimeout(()=>{if(micSession===s){stopRecording();toast('录音已保存到草稿')}},limit*1000);
      log('mic_start',{target:targetAtStart,mode:modeAtStart,device:stream.getAudioTracks()[0]?.label,mime:recorder.mimeType});
    }catch(error){
      if(started){started.failed=true;try{if(started.recorder.state==='recording')started.recorder.stop()}catch{}cleanupMic(started)}
      else{stream?.getTracks().forEach(t=>t.stop());try{source?.disconnect();analyser?.disconnect()}catch{}}
      micPending=false;updateRecording();toast('无法录音，请检查麦克风权限和设备');log('mic_error',{name:error.name,message:error.message},{level:'error'});
    }
  }

  const clarificationRoot=create('div','clarification');clarificationRoot.id='clarification';clarificationRoot.hidden=true;$('drafts').before(clarificationRoot);
  document.querySelectorAll('.theme-dot').forEach(b=>b.onclick=()=>setTheme(b.dataset.theme));
  $('clearTarget').onclick=()=>setTarget('band');$('instrumentName').onclick=()=>setTarget(focused());
  $('inputModes').querySelectorAll('button').forEach(b=>b.onclick=()=>{
    if (micSession?.wake && b.dataset.mode !== 'speech') closeWakeMic(micSession);
    setMode(b.dataset.mode);
    if (b.dataset.mode === 'speech') { wakeDisabled=false; void startWakeMic(); }
  });
  $('prev').onclick=()=>moveTo(focusIndex-1);$('next').onclick=()=>moveTo(focusIndex+1);
  $('play').onclick=togglePlayback;$('tempoBtn').onclick=()=>$('tempoPopover').hidden?openTempo():closeTempo();
  $('tempoBtn').addEventListener('wheel',wheelTempo,{passive:false});$('bpmInput').addEventListener('wheel',e=>{if(!e.deltaY)return;e.preventDefault();adjustTempo((e.deltaY<0?1:-1)*(e.shiftKey?5:1))},{passive:false});
  $('mixerBtn').onclick=()=>$('mixerExtension').hidden?openMixer():closeMixer();$('mixerClose').onclick=()=>closeMixer();$('tempoClose').onclick=()=>closeTempo();$('tempoForm').onsubmit=applyTempo;$('tempoDown').onclick=()=>adjustTempo(-1);$('tempoUp').onclick=()=>adjustTempo(1);$('tapTempo').onclick=tapTempo;
  $('txt').oninput=syncComposer;$('dock').onsubmit=sendInput;$('mic').onclick=toggleMic;
  $('attachBtn').onclick=()=>$('fileInput').click();$('fileInput').onchange=e=>importFiles([...e.target.files]);
  $('stage').addEventListener('pointerdown',e=>{if(e.button>0||e.target.closest('button,input,select,[role=slider]'))return;swipe={x:e.clientX,y:e.clientY,id:e.pointerId}});
  window.addEventListener('pointerup',e=>{if(swipe?.id!==e.pointerId)return;const dx=e.clientX-swipe.x,dy=e.clientY-swipe.y;swipe=null;if(Math.abs(dx)>45&&Math.abs(dx)>Math.abs(dy)*1.3)moveTo(focusIndex+(dx<0?1:-1))});
  window.addEventListener('pointercancel',()=>swipe=null);
  $('stage').addEventListener('wheel',e=>{if(Math.abs(e.deltaX)<Math.abs(e.deltaY)*1.2||Math.abs(e.deltaX)<8||e.target.closest('input,.keyboard,[role=slider]'))return;e.preventDefault();if(performance.now()-wheelStamp>380){wheelStamp=performance.now();moveTo(focusIndex+(e.deltaX>0?1:-1))}},{passive:false});
  document.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files')){e.preventDefault();$('dock').classList.add('drop-active')}});
  document.addEventListener('dragleave',e=>{if(!e.relatedTarget)$('dock').classList.remove('drop-active')});
  document.addEventListener('drop',e=>{if(e.dataTransfer.files.length){e.preventDefault();$('dock').classList.remove('drop-active');importFiles([...e.dataTransfer.files])}});

  const typingMap={a:0,w:1,s:2,e:3,d:4,f:5,t:6,g:7,y:8,h:9,u:10,j:11,k:12,o:13,l:14,p:15};

  document.addEventListener('keydown',e=>{
    if(e.target.closest('#ins'))return;
    if(!$('mixerExtension').hidden&&e.target.closest('#mixerExtension,#mixerBtn')){if(e.key==='Escape'){e.preventDefault();closeMixer()}return}
    if(!$('tempoPopover').hidden&&e.key==='Escape'&&e.target.closest('#tempoPopover,#tempoBtn')){e.preventDefault();closeTempo();return}
    if(e.target.closest('#tempoPopover,#tempoBtn'))return;
    if(e.target.closest('input,textarea,select,[role=slider]')||e.ctrlKey||e.altKey||e.metaKey||e.isComposing)return;
    if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();moveTo(focusIndex+(e.key==='ArrowLeft'?-1:1));return}
    const key=e.key.toLowerCase();
    if(focused()==='keys'&&!e.repeat&&(!e.target.closest('button')||e.target.closest('.white-key,.black-key'))&&typingMap[key]!==undefined){const note=(octave+1)*12+typingMap[key];e.preventDefault();typingHeld.set(key,note);noteOn(note,90,'typing:'+key)}
  });
  document.addEventListener('keyup',e=>{const key=e.key.toLowerCase(),note=typingHeld.get(key);if(note!==undefined){noteOff(note,'typing:'+key);typingHeld.delete(key)}});
  window.addEventListener('blur',()=>{releaseAllNotes();swipe=null});
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'){releaseAllNotes();stopMidiPreview();if(micSession){log('mic_background_stop',{});if(micSession.wake)closeWakeMic(micSession);else stopRecording()}}else void autoStartWake()});
  const resize=()=>{const n=$('stage').clientWidth<560?7:14;if(n!==whiteCount){releaseScreenNotes();whiteCount=n;keyboardMarkup()}renderStage()};
  window.addEventListener('resize',resize);
  if(window.ResizeObserver)new ResizeObserver(resize).observe($('stage'));
  window.addEventListener('pagehide',()=>{releaseAllNotes();if(micSession?.wake)closeWakeMic(micSession);else if(micSession){const s=micSession;s.failed=true;try{s.recorder.stop()}catch{}cleanupMic(s)}audio.dispose();for(const d of drafts)if(d.url)URL.revokeObjectURL(d.url);try{unsubscribe?.()}catch{}});
  let theme='burgundy';try{const saved=localStorage.getItem('jammate-theme');if(themes[saved])theme=saved}catch{}
  setTheme(theme,false);buildStage();setTarget('band');setMode('auto');updateMelody();updateTransport();syncComposer();
  if(release)document.body.classList.add('release');
  inspector.connection({connected:false,status:'后端未连接'});
  log('ui_ready',{theme,source:'local-preview',backendConnected:false,bpm:localBpm,meter:localMeter,tempoUnit:tempoUnit(localMeter)});
})();
