import {downloadMrt2Input, downloadMrt2Midi, prepareMrt2Input} from './mrt2-input.js';
import {publishInputEvent} from './input-event-output.js';
import {createRhythmEditor, latestRequest, midiVelocity, playbackSchedule} from './rhythm-editor.js';
import {canAnalyzeOriginalWave, captureWindow, MAX_CAPTURE_SECONDS, pcmWave} from './rhythm-capture.js';

const $ = id => document.getElementById(id);
const analysis = latestRequest(), midiRequest = latestRequest();
const original = $('original'), seek = $('seek');
const midiStatus = $('rhythm-mrt2-status'), editStatus = $('edit-status');
let buffer, sourceWave, sourceURL, eventURL, sessionId, recorder, stream, timer;
let phase = 'idle', disposed = false, operation = 0;
let editor, selected = -1, position = 0, prepared, midiFailed = false;
let playbackContext, playback, playRevision = 0, playPending = false, playFrame;


function sync() {
  const idle = phase === 'idle';
  for (const id of ['file', 'example']) $(id).disabled = !idle;
  $('record').disabled = !idle && phase !== 'recording';
  $('record').textContent = phase === 'recording' ? '停止录音' : '开始录音';
  $('analyze').disabled = !idle || !buffer;
  const ready = idle && Boolean(prepared);
  for (const id of ['play-midi', 'rhythm-mrt2-midi', 'rhythm-mrt2-export']) $(id).disabled = !ready;
  $('play-midi').textContent = playback || playPending ? '暂停 MIDI' : '听当前 MIDI';
  $('retry-midi').hidden = !midiFailed;
  $('retry-midi').disabled = !idle;
}

function setPhase(value) {
  phase = value;
  sync();
}

function setPosition(seconds) {
  const duration = editor?.event.payload.audio_duration_s ?? 0;
  position = Math.max(0, Math.min(duration, seconds));
  seek.value = String(position);
  const text = `${position.toFixed(3)} 秒`;
  $('play-position').textContent = text;
  seek.setAttribute('aria-valuetext', text);
  $('rhythm-cursor').style.left = `${duration ? 100 * position / duration : 0}%`;
}

function stopPlayback() {
  playRevision++;
  playPending = false;
  cancelAnimationFrame(playFrame);
  if (playback) {
    setPosition(playback.position + Math.max(0, playbackContext.currentTime - playback.started));
    playback.master.disconnect();
    for (const voice of playback.voices) voice.stop();
    playback = null;
  }
  sync();
}

function drumVoice(context, master, at, duration, velocity) {
  const wave = context.createOscillator();
  const gain = context.createGain();
  const end = at + duration;
  const attack = at + Math.min(.003, duration / 3);
  const peak = .7 * (velocity / 127) ** 1.35;
  wave.frequency.setValueAtTime(155, at);
  wave.frequency.exponentialRampToValueAtTime(55, end);
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(peak, attack);
  gain.gain.linearRampToValueAtTime(0, end);
  wave.connect(gain).connect(master);
  wave.start(at);
  wave.stop(end);
  return wave;
}

async function playMidi() {
  if (playback || playPending) {
    stopPlayback();
    return;
  }
  if (phase !== 'idle' || !prepared) return;
  if (position >= prepared.duration_s) setPosition(0);
  original.pause();
  const revision = ++playRevision;
  playPending = true;
  sync();
  try {
    const context = playbackContext ??= new AudioContext();
    await context.resume();
    if (revision !== playRevision) return;
    const schedule = playbackSchedule(prepared, position);
    const master = context.createGain();
    master.connect(context.destination);
    const started = context.currentTime + .025;
    const voices = schedule.hits.map(hit => drumVoice(context, master, started + hit.offset, hit.duration, hit.velocity));
    playback = {position, started, master, voices};
    playPending = false;
    midiStatus.textContent = '';
    sync();
    function advance() {
      if (context.currentTime >= started + schedule.duration) {
        stopPlayback();
        return;
      }
      setPosition(playback.position + Math.max(0, context.currentTime - started));
      playFrame = requestAnimationFrame(advance);
    }
    playFrame = requestAnimationFrame(advance);
  } catch (error) {
    if (revision !== playRevision) return;
    stopPlayback();
    midiStatus.textContent = `无法播放 MIDI：${error.message}`;
  }
}

function renderAxis(duration) {
  const marks = [], labels = [];
  const divisions = 4 * Number($('timeline-zoom').value);
  for (let i = 0; i <= divisions; i++) {
    const tick = document.createElement('span'), label = document.createElement('span');
    tick.className = 'rhythm-tick';
    tick.style.left = label.style.left = `${i * 100 / divisions}%`;
    label.textContent = `${Number((i * duration / divisions).toFixed(2))} 秒`;
    marks.push(tick);
    labels.push(label);
  }
  $('rhythm-grid').replaceChildren(...marks);
  $('rhythm-axis').replaceChildren(...labels);
}

function revealPosition() {
  const viewport = $('rhythm-viewport');
  const visible = viewport.getBoundingClientRect();
  const cursor = $('rhythm-cursor').getBoundingClientRect();
  if (cursor.left < visible.left || cursor.right > visible.right)
    viewport.scrollLeft += cursor.left - visible.left - viewport.clientWidth / 2;
}

function selectHit(index, seekToHit = false) {
  const {onset_seconds: times, accents} = editor.event.payload.rhythm;
  selected = index >= 0 && index < times.length ? index : -1;
  $('hit-select').selectedIndex = selected;
  for (const [i, hit] of [...$('rhythm-hits').children].entries())
    hit.setAttribute('aria-pressed', String(i === selected));
  for (const id of ['hit-select', 'hit-time', 'hit-velocity', 'save-hit', 'delete-hit'])
    $(id).disabled = selected === -1;
  $('hit-time').value = selected === -1 ? '' : String(times[selected]);
  $('hit-velocity').value = selected === -1 ? '' : String(midiVelocity(accents[selected]));
  if (seekToHit && selected !== -1) {
    stopPlayback();
    setPosition(times[selected]);
    revealPosition();
  }
}

function renderHits() {
  const {rhythm, audio_duration_s: duration} = editor.event.payload;
  const {onset_seconds: times, accents} = rhythm;
  const buttons = [], options = [];
  for (const [index, time] of times.entries()) {
    const label = `拍点 ${index + 1} · ${time.toFixed(3)} 秒`;
    const button = document.createElement('button'), bar = document.createElement('span');
    button.type = 'button';
    button.className = 'rhythm-hit';
    button.style.left = `${time / duration * 100}%`;
    // Shrink the hit target as well as the visible bar so adjacent hits cannot
    // cover one another. Zoom changes only display scale, never source timing.
    const gap = Math.min(time - (times[index - 1] ?? -duration),
      (times[index + 1] ?? time + duration) - time);
    button.style.setProperty('--hit-space', `${90 * gap / duration}%`);
    button.setAttribute('aria-label', `${label} · 相对力度 ${midiVelocity(accents[index])}`);
    button.title = button.getAttribute('aria-label');
    button.onclick = () => selectHit(index, true);
    bar.style.height = `${accents[index] * 100}%`;
    button.append(bar);
    buttons.push(button);
    options.push(new Option(label, String(index)));
  }
  $('rhythm-hits').replaceChildren(...buttons);
  $('hit-select').replaceChildren(...options);
  $('no-hits').hidden = times.length > 0;
  $('undo-hit').disabled = !editor.canUndo;
  $('summary').textContent = `${times.length} 个拍点 · ${duration.toFixed(2)} 秒`;
  selectHit(selected);
}

async function post(path, body, signal) {
  const response = await fetch(`/api/input/${path}`, {
    method: 'POST', signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}

function prepareMidi() {
  midiRequest.cancel();
  prepared = null;
  midiFailed = false;
  const event = editor?.event;
  // An empty edited rhythm is a valid silent MIDI, with the full take duration.
  midiStatus.textContent = event ? '正在准备当前节奏的 MIDI…' : '';
  sync();
  if (!event) return;

  return midiRequest.run(signal => post('rhythm/midi', {event}, signal), result => {
    prepared = result;
    midiStatus.textContent = '';
    sync();
  }, error => {
    midiFailed = true;
    midiStatus.textContent = `MIDI 准备失败：${error.message}`;
    sync();
  });
}

function saveMidi(download) {
  if (phase !== 'idle' || !prepared) return;
  download(prepareMrt2Input('rhythm_hits', {midi: prepared.midi, duration: prepared.duration_s}));
}

function refreshEvent() {
  original.pause();
  stopPlayback();
  const event = editor.event;
  const documentText = JSON.stringify(event, null, 2);
  if (eventURL) URL.revokeObjectURL(eventURL);
  eventURL = URL.createObjectURL(new Blob([documentText], {type: 'application/json'}));
  $('download-event').href = eventURL;
  $('download-event').download = `rhythm-${event.event_id}.input-event.json`;
  $('event').textContent = documentText;
  publishInputEvent(event);
  renderHits();
  prepareMidi();
}

function commitEdit(revise, nextSelection, seekToHit = false) {
  try {
    const previous = editor.event;
    const event = revise();
    if (previous !== event) {
      selected = nextSelection(event.payload.rhythm.onset_seconds);
      refreshEvent();
      if (seekToHit) setPosition(event.payload.rhythm.onset_seconds[selected]);
      editStatus.textContent = '已更新当前节奏。';
    } else {
      editStatus.textContent = '拍点没有改动。';
    }
  } catch (error) {
    editStatus.textContent = error.message;
  }
}

function clearResult() {
  stopPlayback();
  editor = null;
  selected = -1;
  setPosition(0);
  prepareMidi();
  if (eventURL) URL.revokeObjectURL(eventURL);
  eventURL = null;
  $('download-event').removeAttribute('href');
  $('event').textContent = '';
  editStatus.textContent = '';
  $('result').hidden = true;
}

function clearAudio() {
  clearResult();
  buffer = null;
  sourceWave = null;
  sessionId = null;
  original.pause();
  original.removeAttribute('src');
  original.load();
  if (sourceURL) URL.revokeObjectURL(sourceURL);
  sourceURL = null;
  $('original-source').hidden = $('download-original').hidden = true;
  $('download-original').removeAttribute('href');
}

async function load(blob, token, {microphone = false} = {}) {
  if (token !== operation) return;
  if (blob.size > 15 * 1024 * 1024) throw new Error('请使用 15 MB 以内的音频。');
  const bytes = await blob.arrayBuffer();
  const keepWave = canAnalyzeOriginalWave(bytes);
  const context = new AudioContext();
  let decoded;
  try {
    decoded = await context.decodeAudioData(keepWave ? bytes.slice(0) : bytes);
  } finally {
    await context.close();
  }
  if (token !== operation) return;
  const take = captureWindow(decoded, {microphone});
  buffer = take.audio;
  sourceWave = keepWave ? bytes : null;
  if (take.capped) {
    sourceWave = pcmWave(buffer);
    blob = new Blob([sourceWave], {type: 'audio/wav'});
  }
  sessionId = crypto.randomUUID();
  sourceURL = URL.createObjectURL(blob);
  original.src = sourceURL;
  $('download-original').href = sourceURL;
  const extension = blob.type.includes('wav') ? 'wav' : blob.type.includes('mp4') ? 'm4a' :
    blob.type.includes('ogg') ? 'ogg' : 'webm';
  $('download-original').download = blob instanceof File ? blob.name : `rhythm-original.${extension}`;
  $('original-source').hidden = $('download-original').hidden = false;
  $('status').textContent = take.capped ? '已保存录音的前 30 秒，达到录制上限。' :
    `已载入 ${buffer.duration.toFixed(2)} 秒录音。`;
}

function reportError(error) {
  $('status').textContent = error.name === 'NotAllowedError' ? '麦克风权限未开启，可以允许权限或导入录音。' :
    error.name === 'EncodingError' ? '无法解码这段音频，请使用有效的 WAV、MP3 或 WebM 文件。' : error.message;
}

async function replaceAudio(getBlob) {
  if (phase !== 'idle') return;
  const token = ++operation;
  setPhase('loading');
  clearAudio();
  $('status').textContent = '正在载入音频…';
  try {
    await load(await getBlob(), token);
  } catch (error) {
    if (token === operation) reportError(error);
  } finally {
    if (token === operation) setPhase('idle');
  }
}

function releaseMicrophone() {
  clearTimeout(timer);
  stream?.getTracks().forEach(track => track.stop());
  stream = null;
}

function stopRecording() {
  if (phase !== 'recording' || recorder?.state !== 'recording') return;
  setPhase('stopping');
  recorder.stop();
}

function wavBase64(data) {
  const bytes = new Uint8Array(data);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

$('file').onchange = () => {
  const file = $('file').files[0];
  $('file').value = '';
  if (file) replaceAudio(() => file);
};
$('example').onclick = () => replaceAudio(async () => {
  const response = await fetch('free-rhythm-example.wav');
  if (!response.ok) throw new Error('未找到节奏示例。');
  return response.blob();
});
$('record').onclick = async () => {
  if (phase === 'recording') {
    stopRecording();
    return;
  }
  if (phase !== 'idle') return;
  const token = ++operation;
  setPhase('requesting');
  clearResult();
  original.pause();
  $('status').textContent = '正在请求麦克风权限…';
  try {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
      throw new Error('浏览器无法录音，请在本机 Chrome／Edge 中打开，或导入音频。');
    const acquired = await navigator.mediaDevices.getUserMedia({audio: {
      echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    }});
    if (disposed || token !== operation) {
      acquired.getTracks().forEach(track => track.stop());
      return;
    }
    stream = acquired;
    const capture = new MediaRecorder(stream);
    const chunks = [];
    recorder = capture;
    capture.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    capture.onerror = () => {
      if (recorder !== capture) return;
      recorder = null;
      releaseMicrophone();
      setPhase('idle');
      $('status').textContent = '录音中断，请重新录制或导入。';
    };
    capture.onstop = async () => {
      if (recorder !== capture) return;
      recorder = null;
      releaseMicrophone();
      setPhase('loading');
      try {
        await load(new Blob(chunks, {type: capture.mimeType}), token, {microphone: true});
      } catch (error) {
        if (token === operation) reportError(error);
      } finally {
        if (token === operation) setPhase('idle');
      }
    };
    capture.start();
    clearAudio();
    setPhase('recording');
    timer = setTimeout(stopRecording, MAX_CAPTURE_SECONDS * 1000);
    $('status').textContent = '正在录音，可以按自己的节奏拍击。';
  } catch (error) {
    if (token !== operation) return;
    recorder = null;
    releaseMicrophone();
    reportError(error);
    setPhase('idle');
  }
};
$('analyze').onclick = () => {
  if (phase !== 'idle' || !buffer) return;
  setPhase('analyzing');
  clearResult();
  original.pause();
  $('status').textContent = '正在识别录音中的拍点…';
  return analysis.run(signal => post('rhythm', {
    wav: wavBase64(sourceWave ?? pcmWave(buffer)), session_id: sessionId, started_at: null,
  }, signal),
    event => {
      editor = createRhythmEditor(event);
      const {audio_duration_s: duration, status} = event.payload;
      seek.max = String(duration);
      setPosition(0);
      $('timeline-zoom').value = '1';
      $('rhythm-roll').style.removeProperty('--timeline-width');
      $('rhythm-viewport').scrollLeft = 0;
      renderAxis(duration);
      selected = status === 'ready' ? 0 : -1;
      refreshEvent();
      $('result').hidden = false;
      setPhase('idle');
      $('status').textContent = status === 'ready' ? '已识别拍点，可以试听或编辑。' :
        '未识别到拍点。可以根据原录音手动添加，或重新录制。';
    }, error => {
      clearResult();
      setPhase('idle');
      $('status').textContent = `识别失败：${error.message}`;
    });
};

$('hit-select').onchange = event => selectHit(Number(event.target.value), true);
$('hit-editor').onsubmit = event => {
  event.preventDefault();
  if (!editor || selected < 0) return;
  const time = $('hit-time').valueAsNumber, velocity = $('hit-velocity').valueAsNumber;
  commitEdit(() => editor.save(selected, time, velocity), times => times.indexOf(time), true);
};
$('delete-hit').onclick = () => {
  if (!editor || selected < 0) return;
  const index = selected;
  commitEdit(() => editor.remove(index), times => Math.min(index, times.length - 1));
};
$('add-hit').onclick = () => {
  if (!editor) return;
  stopPlayback();
  const time = position;
  commitEdit(() => editor.save(null, time, 80), times => times.indexOf(time));
};
$('undo-hit').onclick = () => {
  if (editor?.canUndo) commitEdit(() => editor.undo(), times => Math.min(Math.max(0, selected), times.length - 1));
};
seek.oninput = () => {
  const next = seek.valueAsNumber;
  stopPlayback();
  setPosition(next);
  revealPosition();
};
$('timeline-zoom').onchange = () => {
  $('rhythm-roll').style.setProperty('--timeline-width', `${Number($('timeline-zoom').value) * 100}%`);
  renderAxis(editor?.event.payload.audio_duration_s ?? 0);
  revealPosition();
};
$('play-midi').onclick = playMidi;
$('rhythm-mrt2-midi').onclick = () => saveMidi(downloadMrt2Midi);
$('rhythm-mrt2-export').onclick = () => saveMidi(downloadMrt2Input);
$('retry-midi').onclick = prepareMidi;
original.addEventListener('play', stopPlayback);
window.addEventListener('pagehide', event => {
  disposed = true;
  operation++;
  analysis.cancel();
  midiRequest.cancel();
  recorder = null;
  releaseMicrophone();
  stopPlayback();
  original.pause();
  if (!event.persisted) {
    if (sourceURL) URL.revokeObjectURL(sourceURL);
    if (eventURL) URL.revokeObjectURL(eventURL);
    if (playbackContext) void playbackContext.close();
  } else if (phase !== 'idle') {
    $('status').textContent = buffer ? '录音仍可重新识别。' : '录音或导入音频后开始识别。';
  }
  setPhase('idle');
});
window.addEventListener('pageshow', () => {
  disposed = false;
  sync();
  if (!prepared && editor?.event.payload.status === 'ready') prepareMidi();
});
sync();
