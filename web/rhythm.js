const $=id=>document.getElementById(id);
let buffer, sourceURL, eventURL, recorder, stream, timer, busy=false, startedAt=0;
let sessionId;
function sync(){for(const id of ['record','file','example','count','tempo','origin'])$(id).disabled=busy; $('analyze').disabled=busy||!buffer||!!recorder;}
function clearResult(){ $('result').hidden=true;if(eventURL)URL.revokeObjectURL(eventURL);eventURL=null;$('download').removeAttribute('href'); }
async function load(blob){
  clearResult();buffer=null;sync();if(blob.size>15*1024*1024)throw Error('请使用 15 MB 以内的音频');
  const ctx=new AudioContext();try{buffer=await ctx.decodeAudioData(await blob.arrayBuffer());}finally{await ctx.close();}
  if(buffer.duration<.5||buffer.duration>30){buffer=null;throw Error('录音需为 0.5–30 秒');}
  if(sourceURL)URL.revokeObjectURL(sourceURL);sourceURL=URL.createObjectURL(blob);$('original').src=sourceURL;
  sessionId=crypto.randomUUID();startedAt=0; $('status').textContent=`已载入 ${buffer.duration.toFixed(1)} 秒录音。`;sync();
}
function wavBase64(audio){
  const n=audio.length, data=new ArrayBuffer(44+n*2),v=new DataView(data);
  const text=(at,s)=>[...s].forEach((c,i)=>v.setUint8(at+i,c.charCodeAt(0)));
  text(0,'RIFF');v.setUint32(4,36+n*2,true);text(8,'WAVEfmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,audio.sampleRate,true);v.setUint32(28,audio.sampleRate*2,true);v.setUint16(32,2,true);v.setUint16(34,16,true);text(36,'data');v.setUint32(40,n*2,true);
  for(let i=0;i<n;i++){let s=0;for(let c=0;c<audio.numberOfChannels;c++)s+=audio.getChannelData(c)[i]/audio.numberOfChannels;v.setInt16(44+i*2,Math.round(Math.max(-1,Math.min(1,s))*32767),true);}
  const bytes=new Uint8Array(data);let textData='';for(let i=0;i<bytes.length;i+=8192)textData+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(textData);
}
$('file').onchange=async()=>{try{if($('file').files[0])await load($('file').files[0]);}catch(e){$('status').textContent=e.message;}finally{sync();}};
$('example').onclick=async()=>{try{const r=await fetch('rhythm-example.wav');if(!r.ok)throw Error('先运行 python examples/make_input_examples.py');await load(await r.blob());}catch(e){$('status').textContent=e.message;}};
$('record').onclick=async()=>{
  if(recorder){recorder.stop();return;}
  try{stream=await navigator.mediaDevices.getUserMedia({audio:true});const chunks=[];recorder=new MediaRecorder(stream);
    recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};recorder.onstop=async()=>{clearTimeout(timer);stream.getTracks().forEach(t=>t.stop());const type=recorder.mimeType;recorder=null;$('record').textContent='开始录音';try{await load(new Blob(chunks,{type}));}catch(e){$('status').textContent=e.message;}sync();};
    recorder.start();timer=setTimeout(()=>recorder?.stop(),20000);$('record').textContent='停止录音';$('status').textContent='正在录音：四下均匀定速，接着一小节。';sync();
  }catch(e){stream?.getTracks().forEach(t=>t.stop());$('status').textContent=e.message;}
};
for(const id of ['count','tempo','origin'])$(id).onchange=clearResult;
$('analyze').onclick=async()=>{
  busy=true;clearResult();sync();$('status').textContent='正在本机分析，首次调用可能需要稍等…';
  try{const r=await fetch('/api/rhythm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({wav:wavBase64(buffer),session_id:sessionId,started_at:startedAt,count_in:Number($('count').value),tempo:Number($('tempo').value),origin:$('origin').value===''?null:Number($('origin').value)})});
    const event=await r.json();if(!r.ok)throw Error(event.error);const rhythm=event.payload.rhythm;
    $('event').textContent=JSON.stringify(event,null,2);$('summary').textContent=`${rhythm.tempo_bpm.toFixed(1)} BPM · 拍点 [${rhythm.onset_beats.join(', ')}] · 网格贴合度 ${rhythm.confidence.toFixed(2)}`;
    eventURL=URL.createObjectURL(new Blob([JSON.stringify(event,null,2)],{type:'application/json'}));$('download').href=eventURL;$('result').hidden=false;$('status').textContent='已生成独立的 rhythm_seed InputEvent。';
  }catch(e){$('status').textContent=`分析失败：${e.message}`;}finally{busy=false;sync();}
};
window.addEventListener('pagehide',()=>{clearTimeout(timer);stream?.getTracks().forEach(t=>t.stop());if(sourceURL)URL.revokeObjectURL(sourceURL);if(eventURL)URL.revokeObjectURL(eventURL);});
