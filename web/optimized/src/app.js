import { transcribeHummingFrames } from './transcribe-humming.js';
import { noteDifferences } from './note-differences.js';
import { hummingEvent } from './input-event.js';
import { createMelodyEditor } from './melody-editor.js';
import { attachNoteControls } from './note-controls.js';
import { publishInputEvent } from '../../input-event-output.js';
import { attachBandPanel } from '../../band-panel.js';
import { downloadMrt2Input, prepareMrt2Input } from '../../mrt2-input.js';
const selectBandInput = attachBandPanel(document.querySelector('#band-panel'));
import * as tf from '@tensorflow/tfjs';
import { BasicPitch } from '@spotify/basic-pitch';
import { SAMPLE_RATE, measureAudio, prepareSamples } from './audio-pipeline.js';
import { APP_VERSION, TIMBRES, createMelodyDocument, buildExports } from './melody.js';

const $ = id => document.getElementById(id);
$('pipeline-version').textContent = `哼唱识别 v${APP_VERSION}`;
const ui = Object.fromEntries(['record','record-rings','mic-icon','stop-icon','record-timer','record-prompt','input-level','example','import','file','original-track','original','source-name','source-duration','convert','status','progress','model-badge','model-label','retry-model','empty','result-content','note-count','result-time','piano-roll','play-midi','timbre','download-midi','conversion-time','download-original','download-json','quality-panel','quality-summary','quality-metrics','processing-summary','cleanup-summary','result-state','overlap-hint'].map(id => [id,$(id)]));
let eventURL = null;
const MAX_RECORD_SECONDS = 20;
const MAX_AUDIO_SECONDS = 30;
let captureEpoch = 0, operation = 0, inferenceRunning = false;
let model = null, modelReady = false, busy = false, requestingMic = false;
let recorder = null, recording = false, stream = null;
let audioCtx = null, analyser = null, micSource = null, meterFrame = 0, recordClock = 0, recordingStart = 0;
let audioBuffer = null, audioURL = null, midiURL = null, midiBytes = null;
let sourceInfo = null, sourceMetrics = null, monoSamples = null, prepared = null, melodyDocument = null, jsonURL = null;
let previousResult = null, regions = [], importFiles = [];
let midiNotes = [], playbackNotes = [], voices = [], playFrame = 0, playing = false, playStart = 0;
let playbackMaster = null;
let trackDuration = 0, graphGeometry = null, playRequest = 0;
let editors = {}, preview = null, confirmed = null;
const formatTime = value => `${Math.floor(value/60).toString().padStart(2,'0')}:${Math.floor(value%60).toString().padStart(2,'0')}`;
const noteName = midi => ['C','C♯','D','D♯','E','F','F♯','G','G♯','A','A♯','B'][midi%12] + (Math.floor(midi/12)-1);
const activeEditor = () => editors[$('revision').value];
const noteControls = attachNoteControls({getEditor:activeEditor,getDuration:()=>melodyDocument.source.durationSec,
  noteName,onSelect:()=>drawRoll(),onChange:id=>{
    $('region').value = '';
    updatePreview(id);
  }});

function status(message, error = false) {
  ui.status.textContent = message;
  ui.status.classList.toggle('error', error);
  ui.status.setAttribute('role', error ? 'alert' : 'status');
}

function syncUI() {
  ui.record.disabled = busy || requestingMic;
  ui.example.disabled = busy || recording || requestingMic;
  ui.import.disabled = busy || recording || requestingMic;
  ui.convert.disabled = inferenceRunning || busy || recording || requestingMic || !!melodyDocument || !prepared || prepared.metadata.quality.status === 'rejected' || !modelReady;
  ui.timbre.disabled = busy || recording || requestingMic;
  $('revision').disabled = $('take').disabled = $('region').disabled = busy || recording || requestingMic;
  ui['play-midi'].disabled = busy || recording || !playbackNotes.length;
  $('humming-mrt2-export').disabled = busy || recording || requestingMic || !confirmed?.notes.length;
  const published = !!confirmed && confirmed.result === activeEditor()?.result && confirmed.timbre === ui.timbre.value;
  $('confirm-melody').disabled = busy || recording || !preview || published;
  $('edit-status').textContent = published ? '已确认，下载与当前音轨一致。' :
    confirmed ? '当前为草稿，下载仍使用上次确认的版本。' : '草稿可试听，确认后可下载。';
  ui.convert.firstElementChild.textContent = busy ? '正在转换…' : melodyDocument ? '已生成音轨' : '转成 MIDI';
  ui.record.setAttribute('aria-label', recording ? '停止录音' : '开始录音');
  ui['mic-icon'].hidden = recording;
  ui['stop-icon'].hidden = !recording;
  ui['record-rings'].classList.toggle('recording', recording);
}

async function getContext() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) throw new Error('浏览器不支持音频处理，请使用新版 Chrome 或 Edge。');
  if (!audioCtx || audioCtx.state === 'closed') audioCtx = new AudioContextClass();
  if (audioCtx.state === 'suspended') await audioCtx.resume();
  return audioCtx;
}

async function loadModel() {
  modelReady = false;
  ui['retry-model'].hidden = true;
  ui['model-badge'].className = 'model-badge';
  ui['model-label'].textContent = '加载 Basic Pitch…';
  try {
    // Repeatable reference backend: Windows WebGL runs differed on the same
    // synthetic input. Keep the baseline untouched for an honest comparison.
    await tf.setBackend('cpu');
    await tf.ready();
    model = new BasicPitch('./model/model.json');
    await model.model;
    modelReady = true;
    ui['model-badge'].classList.add('ready');
    ui['model-label'].textContent = 'Basic Pitch 已就绪';
  } catch (error) {
    console.error('Basic Pitch model load failed', error);
    ui['model-badge'].classList.add('failed');
    ui['model-label'].textContent = '模型加载失败';
    ui['retry-model'].hidden = false;
    status('模型暂时没有加载成功，请点“重新加载模型”。', true);
  }
  syncUI();
}

function clearResult() {
  selectBandInput(null);
  stopPlayback();
  midiNotes = []; playbackNotes = []; midiBytes = null;
  if (midiURL) URL.revokeObjectURL(midiURL);
  midiURL = null;
  if (eventURL) URL.revokeObjectURL(eventURL);
  eventURL = null;
  $('download-event').removeAttribute('href');
  melodyDocument = null;
  previousResult = null; regions = []; editors = {}; preview = confirmed = null; graphGeometry = null;
  $('revision').value = 'current';
  $('region').replaceChildren(new Option('全段',''));
  $('region-picker').hidden = true;
  if (jsonURL) URL.revokeObjectURL(jsonURL);
  jsonURL = null;
  ui['download-midi'].removeAttribute('href');
  ui['download-json'].removeAttribute('href');
  for (const id of ['download-midi','download-json','download-event']) $(id).setAttribute('aria-disabled','true');
  ui['result-content'].hidden = true;
  ui['empty'].hidden = false;
  ui['note-count'].hidden = true;
}

function clearAudio() {
  clearResult(); ui.original.pause();
  if (audioURL) URL.revokeObjectURL(audioURL);
  audioURL = null; audioBuffer = null; sourceInfo = null; sourceMetrics = null; monoSamples = null; prepared = null;
  ui.original.removeAttribute('src'); ui.original.load();
  ui['download-original'].removeAttribute('href');
  ui['original-track'].hidden = true; ui['quality-panel'].hidden = true;
  ui['record-timer'].textContent = '00:00';
  ui['record-prompt'].textContent = '点一下，开始哼唱';
}

function refreshPreparation() {
  if (!monoSamples) return;
  clearResult();
  prepared = prepareSamples(monoSamples, {normalize:false, trim:false, sourceMetrics});
  const {quality, processing} = prepared.metadata;
  ui['quality-panel'].hidden = false;
  ui['quality-panel'].dataset.quality = quality.status;
  ui['quality-summary'].textContent = quality.warnings.length ? quality.warnings.map(warning=>warning.message).join(' ') : '音量检查通过。请回听确认环境声是否影响旋律。';
  const db = value => value > 0 ? `${(20*Math.log10(value)).toFixed(1)} dBFS` : '−∞ dBFS';
  ui['quality-metrics'].textContent = `原音频峰值 ${db(quality.sourceMetrics.peak)} · 平均电平 ${db(quality.sourceMetrics.rms)}`;
  ui['processing-summary'].textContent = `识别输入：单声道 · 22,050 Hz · Float32。增益 ×${processing.gain.toFixed(2)}；裁掉开头 ${processing.trimStartSec.toFixed(2)} 秒、结尾 ${processing.trimEndSec.toFixed(2)} 秒。时间轴仍从原录音开始。`;
  syncUI();
}

async function setAudio(blob, name, sourceType = 'recording', token = operation) {
  if (token !== operation) return;
  clearAudio();
  // Decode directly at the model rate. Going through the playback device rate
  // first would resample an already-22,050 Hz recording twice.
  const decoder = new OfflineAudioContext(1, 1, SAMPLE_RATE);
  const decoded = await decoder.decodeAudioData(await blob.arrayBuffer());
  if (decoded.duration < .5) throw new Error('录音太短了，试着哼 3–8 秒再转换。');
  if (decoded.duration > MAX_AUDIO_SECONDS + .2) throw new Error('请导入 30 秒以内的录音；短一点更方便比较效果。');
  if (token !== operation) return;
  const resampled = decoded.numberOfChannels === 1 ? decoded.getChannelData(0) : await monoResample(decoded);
  if (token !== operation) return;
  audioBuffer = decoded;
  sourceInfo = {id:crypto.randomUUID(),sessionId:crypto.randomUUID(),name,kind:sourceType,mimeType:blob.type || 'application/octet-stream',
    byteLength:blob.size,durationSec:decoded.duration,decodedSampleRateHz:decoded.sampleRate,decodedChannels:decoded.numberOfChannels};
  sourceMetrics = measureAudio(Array.from({length:decoded.numberOfChannels},(_,index)=>decoded.getChannelData(index)));
  monoSamples = resampled;
  audioURL = URL.createObjectURL(blob);
  const extension = blob.type.includes('wav')?'wav':blob.type.includes('mp4')?'m4a':blob.type.includes('ogg')?'ogg':blob.type.includes('mpeg')?'mp3':'webm';
  ui['download-original'].href = audioURL;
  ui['download-original'].download = sourceType === 'import' ? name : `jammate-${sourceInfo.id.slice(0,8)}-original.${extension}`;
  ui.original.src = audioURL;
  ui.original.load();
  ui['original-track'].hidden = false;
  ui['source-name'].textContent = name;
  ui['source-duration'].textContent = `${decoded.duration.toFixed(1)} 秒`;
  ui['record-timer'].textContent = formatTime(decoded.duration);
  ui['record-prompt'].textContent = '录音已准备好，也可以重新录一段';
  refreshPreparation();
  status(prepared.metadata.quality.status === 'rejected' ? '录音几乎没有声音，已保留原文件，请重新录制或导入。' : sourceType === 'example' ? '已载入合成旋律示例，可先听原声，再点“转成 MIDI”。' : '录音已准备好，点“转成 MIDI”试试。',prepared.metadata.quality.status === 'rejected');
  syncUI();
}

function releaseMicrophone() {
  clearInterval(recordClock);
  cancelAnimationFrame(meterFrame);
  stream?.getTracks().forEach(track => track.stop());
  stream = null;
  micSource?.disconnect(); micSource = null; analyser = null;
  ui['input-level'].value = 0;
}

function animateMeter() {
  if (!recording || !analyser) return;
  const values = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(values);
  const rms = Math.sqrt(values.reduce((sum, value) => sum + value*value,0)/values.length);
  ui['input-level'].value = Math.min(1, rms * 5);
  meterFrame = requestAnimationFrame(animateMeter);
}

async function toggleRecording() {
  if (recording) { stopRecording(); return; }
  if (busy || requestingMic) return;
  ui.original.pause(); stopPlayback();
  requestingMic = true; syncUI();
  const epoch = captureEpoch;
  try {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('当前浏览器无法录音。请在 Chrome／Edge 中打开，或使用“导入录音”。');
    const ctx = await getContext();
    const acquired = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false });
    if (epoch !== captureEpoch) { acquired.getTracks().forEach(track => track.stop()); return; }
    stream = acquired;
    const type = ['audio/webm;codecs=opus','audio/mp4','audio/webm','audio/ogg;codecs=opus'].find(value => MediaRecorder.isTypeSupported(value));
    const capture = new MediaRecorder(stream, type ? {mimeType:type} : undefined);
    recorder = capture;
    const chunks = [];
    capture.addEventListener('dataavailable', event => { if (event.data.size) chunks.push(event.data); });
    capture.addEventListener('error', () => {
      if (recorder !== capture) return;
      recorder = null; recording = false; busy = false; releaseMicrophone(); syncUI();
      status('录音中断了，请检查麦克风后重试。', true);
    });
    capture.addEventListener('stop', async () => {
      if (recorder !== capture) return;
      recorder = null; recording = false; busy = true; syncUI();
      const blob = new Blob(chunks, {type:capture.mimeType || 'audio/webm'});
      releaseMicrophone();
      const token = ++operation;
      try { await setAudio(blob, '你的哼唱', 'recording', token); }
      catch (error) { if (token === operation) status(error.message || '没有读到录音，请重新录一段。', true); }
      finally { if (token === operation) { busy = false; syncUI(); } }
    }, { once: true });
    micSource = ctx.createMediaStreamSource(stream);
    analyser = ctx.createAnalyser(); analyser.fftSize = 1024;
    micSource.connect(analyser);
    operation++; setImportFiles([]); clearAudio();
    recording = true;
    recordingStart = performance.now();
    ui['record-timer'].textContent = '00:00';
    ui['record-prompt'].textContent = '正在听你哼唱，再点一下结束';
    status('可以开始哼了。最长录 20 秒，结束后点击方块按钮。');
    recorder.start(250);
    animateMeter();
    recordClock = setInterval(() => {
      const seconds = (performance.now()-recordingStart)/1000;
      ui['record-timer'].textContent = formatTime(seconds);
      if (seconds >= MAX_RECORD_SECONDS) stopRecording();
    },100);
  } catch (error) {
    if (epoch !== captureEpoch) return;
    recorder = null; recording = false;
    releaseMicrophone();
    const denied = error.name === 'NotAllowedError' || error.name === 'SecurityError';
    const missing = error.name === 'NotFoundError';
    status(denied ? '麦克风权限没有开启。请在浏览器地址栏允许麦克风，或用“导入录音”。' : missing ? '没有找到麦克风。可以连接麦克风，或用“导入录音”。' : error.message || '无法开启麦克风，请稍后重试。', true);
  } finally { if (epoch === captureEpoch) { requestingMic = false; syncUI(); } }
}

function stopRecording() {
  if (!recording) return;
  recording = false; busy = true;
  clearInterval(recordClock);
  ui['record-prompt'].textContent = '正在整理录音…';
  if (recorder?.state !== 'inactive') recorder.stop();
  syncUI();
}

async function monoResample(buffer) {
  const offline = new OfflineAudioContext(1, Math.ceil(buffer.duration*SAMPLE_RATE), SAMPLE_RATE);
  const source = offline.createBufferSource(); source.buffer = buffer;
  source.connect(offline.destination); source.start();
  const rendered = await offline.startRendering();
  return rendered.getChannelData(0);
}

async function convert() {
  if (!prepared || prepared.metadata.quality.status === 'rejected' || !modelReady || busy || recording || inferenceRunning) return;
  const token = operation, preparation = prepared, sourceSnapshot = structuredClone(sourceInfo), duration = audioBuffer.duration;
  inferenceRunning = true;
  busy = true; syncUI(); stopPlayback(); ui.original.pause(); clearResult();
  ui.progress.hidden = false; ui.progress.value = 0;
  status('正在听这段旋律…');
  const started = performance.now();
  let scoped = false;
  try {
    const samples = preparation.samples;
    const frames = [], onsets = [];
    await new Promise(resolve => setTimeout(resolve,30));
    tf.engine().startScope(); scoped = true;
    await model.evaluateModel(samples,
      (nextFrames,nextOnsets) => { frames.push(...nextFrames); onsets.push(...nextOnsets); },
      percent => { if (token !== operation) return; ui.progress.value = percent; ui.convert.firstElementChild.textContent = `正在转换 ${Math.round(percent*100)}%`; });
    tf.engine().endScope(); scoped = false;
    if (token !== operation) return;
    if (!frames.length) throw new Error('这段录音没有产生可用结果，请再录一段。');
    const {raw,cleaned,previous} = transcribeHummingFrames(samples,frames,onsets,{comparePrevious:true});
    melodyDocument = createMelodyDocument(sourceSnapshot,preparation.metadata,raw,cleaned,performance.now()-started);
    previousResult = previous;
    regions = noteDifferences(previous.notes,cleaned.notes);
    const describe = notes => notes.map(noteName).join('、') || '无音符';
    for (const [i,r] of regions.entries()) {
      const label = `${r.start.toFixed(2)}–${r.end.toFixed(2)} 秒　${describe(r.before)} → ${describe(r.after)}`;
      $('region').add(new Option(label,String(i)));
    }
    $('region-picker').hidden = !regions.length;
    ui['cleanup-summary'].textContent = regions.length
      ? `旧版 D 有 ${previous.notes.length} 个音，新版有 ${cleaned.notes.length} 个音。可单独试听 ${regions.length} 处变化。`
      : '两个版本的音符与时序一致。';
    melodyDocument.model.executionBackend = tf.getBackend();
    trackDuration = duration;
    editors = {current:createMelodyEditor(cleaned,sourceSnapshot.durationSec),
      previous:createMelodyEditor(previous,sourceSnapshot.durationSec)};
    updatePreview();
    ui['result-time'].textContent = `${trackDuration.toFixed(1)} 秒`;
    ui['conversion-time'].textContent = `转换用时 ${((performance.now()-started)/1000).toFixed(1)} 秒`;
    status(cleaned.notes.length ? '识别完成。点音轨上的音符可修改，试听满意后点「确认旋律」。' : '这段录音没有识别到稳定音符。可以手动添加后试听。');
  } catch (error) {
    if (token !== operation) return;
    console.error('Transcription failed',error);
    status(error.message?.includes('录音') ? error.message : '转换没有完成。可以试一段更短、更清晰的录音，再点“转成 MIDI”。',true);
  } finally {
    if (scoped) tf.engine().endScope();
    inferenceRunning = false;
    if (token === operation) { busy = false; ui.progress.hidden = true; }
    syncUI();
  }
}

function confirmMelody() {
  if (!preview || $('confirm-melody').disabled) return;
  const result = preview;
  const suffix = $('revision').value === 'previous' ? '-previous-D' : '';
  const inputEvent = hummingEvent(result.document,
    {sessionId:sourceInfo.sessionId, startedAt:0, eventId:crypto.randomUUID()});
  confirmed = {result:activeEditor().result,timbre:ui.timbre.value,notes:result.document.notes};
  selectBandInput(confirmed.notes.length ? inputEvent : null);
  if (eventURL) URL.revokeObjectURL(eventURL);
  eventURL = URL.createObjectURL(new Blob([JSON.stringify(inputEvent,null,2)],{type:'application/json'}));
  $('download-event').href = eventURL;
  $('download-event').download = `humming-${sourceInfo.id}${suffix}.input-event.json`;
  midiBytes = result.bytes;
  if (midiURL) URL.revokeObjectURL(midiURL);
  if (jsonURL) URL.revokeObjectURL(jsonURL);
  midiURL = URL.createObjectURL(new Blob([midiBytes],{type:'audio/midi'}));
  jsonURL = URL.createObjectURL(new Blob([JSON.stringify(result.document,null,2)],{type:'application/json'}));
  const basename = `jammate-${sourceInfo.id.slice(0,8)}-humming-${ui.timbre.value}${suffix}`;
  ui['download-midi'].href = midiURL;
  for (const id of ['download-midi','download-json','download-event']) $(id).setAttribute('aria-disabled','false');
  ui['download-midi'].download = `${basename}.mid`;
  ui['download-json'].href = jsonURL; ui['download-json'].download = `${basename}.json`;
  syncUI();
  publishInputEvent(inputEvent);
}

function updatePreview(selectedId) {
  const editor = activeEditor();
  if (!editor) return;
  stopPlayback();
  const document = {...melodyDocument,results:{...melodyDocument.results,cleaned:editor.result}};
  const result = buildExports(document,'cleaned',ui.timbre.value,{modelDynamics:true});
  preview = result;
  playbackNotes = result.playbackNotes;
  // MIDI can reorder simultaneous pitches. Match IDs within each pitch.
  const ids = new Map();
  for (const n of result.document.notes) {
    if (!ids.has(n.pitchMidi)) ids.set(n.pitchMidi,[]);
    ids.get(n.pitchMidi).push(n.id);
  }
  midiNotes = playbackNotes.map(n=>({id:ids.get(n.midi).shift(),pitch:n.midi,start:n.time,duration:n.duration,velocity:n.velocity}));
  noteControls.refresh(selectedId);
  ui['empty'].hidden = true; ui['result-content'].hidden = false;
  ui['note-count'].hidden = false; ui['note-count'].textContent = `${midiNotes.length} 个音符`;
  const gate = result.document.results.cleaned.policy.durationGate;
  if (gate) {
    const tempo = gate.estimatedBpm ? `估计 ${gate.estimatedBpm} BPM` : '拍速未确定';
    ui['result-state'].textContent = `${tempo} · 常规短音门槛 ${Number(gate.normalMs.toFixed(1))} 毫秒 · 保留原节奏`;
  } else {
    ui['result-state'].textContent = '旧版 D · 保留原节奏';
  }
  if (!midiNotes.length) ui['result-state'].textContent += ' · 当前音轨为空';
  const selected = result.document.notes;
  const overlap = selected.some((note,index)=>selected.slice(0,index).some(previous=>previous.pitchMidi!==note.pitchMidi && previous.startSec+previous.durationSec > note.startSec+.03));
  ui['overlap-hint'].hidden = !overlap;
  ui['piano-roll'].hidden = !midiNotes.length;
  drawRoll(); syncUI();
}

function drawRoll(time = -1) {
  if (!midiNotes.length || ui['result-content'].hidden) return;
  const canvas = ui['piano-roll'], width = canvas.clientWidth, height = 205;
  if (!width) return;
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width*ratio) || canvas.height !== Math.round(height*ratio)) { canvas.width = Math.round(width*ratio); canvas.height = Math.round(height*ratio); }
  const c = canvas.getContext('2d'); c.setTransform(ratio,0,0,ratio,0,0); c.clearRect(0,0,width,height);
  const left = 38, right = width-8, top = 13, bottom = height-28;
  const allPitches = [...previousResult.notes,...melodyDocument.results.cleaned.notes,...activeEditor().result.notes].map(n => n.pitchMidi);
  const low = Math.max(0,Math.min(...allPitches)-2), high = Math.min(127,Math.max(...allPitches)+2);
  const x = seconds => left + seconds/trackDuration*(right-left);
  const y = pitch => bottom - (pitch-low)/(high-low)*(bottom-top);
  graphGeometry = {x,y,bars:[]};
  c.font = '10px "Segoe UI",sans-serif'; c.lineWidth = 1;
  const span = high-low, skip = span>20?6:span>10?4:2;
  for (let pitch=low;pitch<=high;pitch++) {
    c.strokeStyle = pitch%12===0?'#d4dcc6':'#e2e7d9'; c.beginPath(); c.moveTo(left,y(pitch)); c.lineTo(right,y(pitch)); c.stroke();
    if ((pitch-low)%skip===0) { c.fillStyle='#939f83'; c.textAlign='right'; c.fillText(noteName(pitch),left-7,y(pitch)+3); }
  }
  const step = trackDuration<=6?1:trackDuration<=15?3:5;
  for (let second=0;second<=trackDuration;second+=step) {
    c.strokeStyle='#e0e5d6'; c.beginPath(); c.moveTo(x(second),top); c.lineTo(x(second),bottom); c.stroke();
    c.fillStyle='#949e85'; c.textAlign=second===0?'left':'center'; c.fillText(`${second}s`,x(second),height-8);
  }
  const noteHeight = Math.min(14,Math.max(4,(bottom-top)/(span+1)*.75));
  for (const note of midiNotes) {
    const active = time>=note.start && time<note.start+note.duration;
    c.fillStyle=active?'#d34d34':note.id===noteControls.selectedId?'#405836':'#899d6c';
    const left=x(note.start), top=y(note.pitch)-noteHeight/2, width=Math.max(2,x(note.start+note.duration)-left-1);
    c.beginPath(); c.roundRect(left,top,width,noteHeight,2); c.fill();
    graphGeometry.bars.push({left,top,width,height:noteHeight,id:note.id});
  }
  if (time>=0) { c.strokeStyle='#d34d34'; c.beginPath(); c.moveTo(x(time),top-4); c.lineTo(x(time),bottom+4); c.stroke(); }
}

function stopPlayback() {
  ++playRequest;
  cancelAnimationFrame(playFrame);
  const ctx = audioCtx;
  for (const {osc,gain} of voices) {
    try { gain.gain.cancelScheduledValues(ctx.currentTime); gain.gain.setTargetAtTime(0,ctx.currentTime,.008); osc.stop(ctx.currentTime+.04); } catch {}
  }
  voices = []; playing = false;
  const previousMaster = playbackMaster; playbackMaster = null;
  if (previousMaster) setTimeout(()=>previousMaster.disconnect(),60);
  ui['play-midi'].innerHTML = '<span aria-hidden="true">▶</span> 听 MIDI';
  drawRoll();
}

async function playMidi() {
  if (playing) { stopPlayback(); return; }
  if (!playbackNotes.length) return;
  ui.original.pause();
  document.querySelector('#band-panel audio').pause();
  const request = ++playRequest;
  const ctx = await getContext();
  if (request !== playRequest) return;
  const region = regions[$('region').value];
  const from = region ? Math.max(0,region.start-.12) : 0;
  const to = region ? Math.min(trackDuration,region.end+.12) : Math.max(trackDuration,...playbackNotes.map(n=>n.time+n.duration));
  playing = true; playStart = ctx.currentTime+.06-from;
  const instrument = ui.timbre.value;
  const master = ctx.createGain(); master.gain.value = .12; master.connect(ctx.destination);
  playbackMaster = master;
  const endTime = playStart+to+.15;
  for (const note of playbackNotes) {
    const onset = Math.max(from,note.time);
    const length = region ? Math.min(note.time+note.duration,to)-onset : note.duration;
    if (length <= 0) continue;
    const osc = ctx.createOscillator(), gain = ctx.createGain();
    osc.type = TIMBRES[instrument].oscillator;
    osc.frequency.value = 440*2**((note.midi-69)/12);
    const start = playStart+onset;
    const level = note.velocity*.7;
    gain.gain.setValueAtTime(0,start); gain.gain.linearRampToValueAtTime(level,start+Math.min(.012,length*.2));
    if (instrument==='pluck') gain.gain.exponentialRampToValueAtTime(.003,start+length);
    else { gain.gain.exponentialRampToValueAtTime(level*.65,start+Math.min(.09,length*.3)); gain.gain.setValueAtTime(level*.65,start+Math.max(length*.5,length-.025)); gain.gain.linearRampToValueAtTime(0,start+length+.04); }
    const filter = ctx.createBiquadFilter(); filter.type='lowpass'; filter.frequency.value=instrument==='bright'?1800:4500;
    osc.connect(gain); gain.connect(filter); filter.connect(master);
    osc.start(start); osc.stop(start+length+.07);
    osc.addEventListener('ended',()=>{osc.disconnect();gain.disconnect();filter.disconnect();},{once:true});
    voices.push({osc,gain});
  }
  ui['play-midi'].innerHTML = '<span aria-hidden="true">■</span> 停止播放';
  const animate = () => {
    if (!playing) { master.disconnect(); return; }
    const time = ctx.currentTime-playStart;
    drawRoll(Math.min(trackDuration,Math.max(0,time)));
    if (ctx.currentTime>endTime) { stopPlayback(); master.disconnect(); return; }
    playFrame=requestAnimationFrame(animate);
  };
  playFrame=requestAnimationFrame(animate);
}

ui.record.addEventListener('click',toggleRecording);
ui.convert.addEventListener('click',convert);
ui['retry-model'].addEventListener('click',loadModel);
ui['play-midi'].addEventListener('click',() => playMidi().catch(error=>{stopPlayback();status('音频播放没有启动，请再点一次“听 MIDI”。',true);console.error(error);}));
ui.original.addEventListener('play',stopPlayback);
document.querySelector('#band-panel audio').addEventListener('play',stopPlayback);
ui.timbre.addEventListener('change',()=>updatePreview());
$('revision').addEventListener('change',()=>updatePreview());
$('confirm-melody').addEventListener('click',confirmMelody);
ui['piano-roll'].addEventListener('click',event=>{
  if (!graphGeometry || !midiNotes.length) return;
  const rect = ui['piano-roll'].getBoundingClientRect();
  const x = event.clientX-rect.left, y = (event.clientY-rect.top)*205/rect.height;
  let nearest = null, distance = Infinity;
  for (const bar of graphGeometry.bars) {
    const dx = Math.max(0,bar.left-x,x-bar.left-bar.width);
    const dy = Math.max(0,bar.top-y,y-bar.top-bar.height);
    if (dx <= 4 && dy <= 4 && dx*dx+dy*dy < distance) {
      nearest = bar; distance = dx*dx+dy*dy;
    }
  }
  if (nearest) { stopPlayback(); noteControls.open(nearest.id); }
});
$('region').addEventListener('change',stopPlayback);
ui.import.addEventListener('click',()=>ui.file.click());
function setImportFiles(files) {
  importFiles = files;
  $('take').replaceChildren(...files.map((file,i) => new Option(`${i+1}. ${file.name}`,String(i))));
  $('take-picker').hidden = files.length < 2;
}
async function loadSelectedFile() {
  const file = importFiles[$('take').selectedIndex];
  if (!file || busy || recording || requestingMic) return;
  const token=++operation; busy=true;syncUI();
  try{clearAudio();if(file.size>15*1024*1024)throw new Error('请使用 15 MB 以内、30 秒以内的短录音。');await setAudio(file,file.name,'import',token);}
  catch(error){if(token!==operation)return;status(error.name === 'EncodingError' ? '无法解码这段音频，请检查文件是否完整，或试试 WAV、MP3、M4A 文件。' : error.message||'无法读取这段音频，请试试 WAV、MP3 或 WebM 文件。',true);}
  finally{if(token===operation){busy=false;syncUI();}}
}
ui.file.addEventListener('change',()=>{
  const files = Array.from(ui.file.files);
  ui.file.value = '';
  if (!files.length || busy || recording || requestingMic) return;
  setImportFiles(files);
  loadSelectedFile();
});
$('take').addEventListener('change',loadSelectedFile);
ui.example.addEventListener('click',async()=>{
  setImportFiles([]);
  const token=++operation; busy=true;syncUI();
  try{clearAudio();const response=await fetch('./example.wav');if(!response.ok)throw new Error('示例加载失败，请稍后再试。');const blob=await response.blob();await setAudio(blob,'合成旋律示例','example',token);}
  catch(error){if(token===operation)status(error.message,true);}
  finally{if(token===operation){busy=false;syncUI();}}
});
new ResizeObserver(()=>drawRoll(playing?Math.max(0,audioCtx.currentTime-playStart):-1)).observe(ui['piano-roll']);
$('humming-mrt2-export').addEventListener('click', () => {
  if (!midiBytes || !confirmed?.notes.length) return;
  downloadMrt2Input(prepareMrt2Input('humming', {midi: midiBytes, duration:trackDuration}));
});
window.addEventListener('pagehide',event=>{
  operation++; ui.progress.hidden = true;
  captureEpoch++; recorder = null; recording = false; busy = false; requestingMic = false;
  releaseMicrophone(); stopPlayback(); ui.original.pause();
  if (!event.persisted) {
    if(audioURL)URL.revokeObjectURL(audioURL);if(midiURL)URL.revokeObjectURL(midiURL);if(jsonURL)URL.revokeObjectURL(jsonURL);
    audioCtx?.close();
  }
  syncUI();
});
window.addEventListener('pageshow',()=>syncUI());
syncUI();
loadModel();
