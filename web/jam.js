const byId = id => document.getElementById(id);
const play = byId('jam-start'), stop = byId('jam-stop');
const bpmInput = byId('jam-bpm'), agent = byId('jam-agent');
const arranger = byId('jam-arranger'), renderer = byId('jam-renderer'), profile = byId('jam-profile');
const timbrePanel = byId('jam-timbres'), timbreStatus = byId('jam-timbre-status');
const timbreSelects = {keys: byId('jam-keys-program'), bass: byId('jam-bass-program')};
const captureMode = byId('jam-input-mode');
const previewAudio = byId('jam-preview-audio'), previewPanel = byId('jam-preview-output');
const backendStatus = byId('jam-backend-status');
const form = byId('jam-feedback'), input = byId('jam-text'), send = byId('jam-send');
const playback = byId('jam-playback'), feedbackStatus = byId('jam-feedback-status');
const microphone = byId('jam-microphone'), voiceStatus = byId('jam-voice-status');
const transcriptPanel = byId('jam-transcript'), transcriptText = byId('jam-transcript-text');
const useTranscript = byId('jam-transcript-use');
const examples = [...form.querySelectorAll('[data-example]')];
const layerNames = {drums: '鼓', bass: '贝斯', keys: '键盘', guitar: '木吉他',
  flute: '长笛', strings: '弦乐', harp: '竖琴'};
const micSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.AudioWorkletNode);
let speechStatus = 'checking';
let musicCapture = false;
let mrtReady = false;
let timbreCatalog = null;
let run = null;

async function post(operation, data, timeout = 4500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch('/api/jam/' + operation, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Jam 服务未响应。');
    return result;
  } finally {
    clearTimeout(timer);
  }
}

function controls() {
  for (const control of [bpmInput, agent, arranger, renderer]) control.disabled = Boolean(run);
  const mrt = renderer.value === 'mrt2';
  const starting = Boolean(run) && !run.started;
  play.disabled = Boolean(run);
  play.textContent = starting ? '正在启动…' : '开始演奏';
  play.setAttribute('aria-busy', String(starting));
  stop.disabled = !run;
  timbrePanel.hidden = renderer.value !== 'fluidsynth' || !timbreCatalog;
  for (const control of [profile, ...Object.values(timbreSelects)]) {
    control.disabled = !timbreCatalog || (Boolean(run) && !run.id);
  }
  byId('jam-mrt2-hint').hidden = !mrt;
  backendStatus.textContent = mrtReady ? '' : 'MRT2 在另一个服务中可用。';
  if (!mrtReady && ['4193', '4195'].includes(location.port) && !mrt) {
    const link = document.createElement('a');
    link.href = 'http://127.0.0.1:4196/jam.html';
    link.textContent = '打开 MRT2 工作台';
    backendStatus.replaceChildren(link);
  }
  const canSend = Boolean(run?.id) && !run.sending;
  for (const control of [input, send, useTranscript, ...examples]) control.disabled = !canSend;
  microphone.disabled = !run?.id || !micSupported || !(musicCapture || speechStatus === 'ready');
  microphone.textContent = run?.voice ? '关闭麦克风' : '开启麦克风';
  microphone.setAttribute('aria-pressed', String(Boolean(run?.voice)));
}

function fillTimbres(catalog) {
  timbreCatalog = catalog;
  if (!catalog) return;
  const families = ['钢琴', '音高打击乐', '风琴', '吉他', '贝斯', '弦乐',
    '合奏', '铜管', '簧管', '木管', '合成主音', '合成铺底', '合成效果',
    '民族乐器', '打击乐', '音效'];
  for (const select of Object.values(timbreSelects)) {
    const groups = families.map(label => {
      const group = document.createElement('optgroup');
      group.label = label;
      return group;
    });
    for (const [id, name] of Object.entries(catalog.programs)) {
      const option = new Option(name + ' · ' + id, id);
      groups[Math.floor(Number(id) / 8)].append(option);
    }
    select.replaceChildren(...groups.filter(group => group.childElementCount));
  }
  selectTimbres(catalog.presets[profile.value], profile.value);
  showTimbres(null);
}

function selectTimbres(programs, preset) {
  if (!programs) return;
  for (const [part, select] of Object.entries(timbreSelects)) select.value = String(programs[part]);
  profile.value = preset ?? (Object.keys(timbreCatalog.presets).find(key => {
    const item = timbreCatalog.presets[key];
    return item.keys === programs.keys && item.bass === programs.bass;
  }) || 'custom');
}

function selectedTimbres() {
  return {keys: Number(timbreSelects.keys.value), bass: Number(timbreSelects.bass.value)};
}

function showTimbres(live) {
  if (!timbreCatalog || timbrePanel.hidden || live?.timbreSending) return;
  const state = live?.timbres;
  let text = '演奏中可以切换，音色在小节起拍时生效。';
  if (state) {
    const names = timbreCatalog.programs;
    text = `当前音色 · ${names[state.active.keys]} / ${names[state.active.bass]}`;
    if (state.pending) text += ` · 新选择将于第 ${state.pending.bar + 1} 小节生效`;
    else if (state.applied_at !== null) text += ` · 第 ${state.applied_at + 1} 小节已切换`;
  }
  if (timbreStatus.textContent !== text) timbreStatus.textContent = text;
}

async function sendTimbres(live) {
  if (!live?.id) return;
  live.queuedTimbres = selectedTimbres();
  if (live.timbreSending) return;
  live.timbreSending = true;
  let failure = null;
  while (run === live && live.queuedTimbres) {
    const programs = live.queuedTimbres;
    live.queuedTimbres = null;
    try {
      const result = await post('timbres', {session: live.id, programs});
      live.timbres = result.timbres;
      failure = null;
    } catch (error) {
      failure = error;
    }
  }
  live.timbreSending = false;
  if (run !== live) return;
  showTimbres(live);
  if (failure) {
    const current = live.timbres?.pending?.programs ?? live.timbres?.active;
    selectTimbres(current);
    timbreStatus.textContent = failure.message;
  }
}

function show(view, live) {
  playback.textContent = view.current_bar === null ? '准备播放' : view.playing ? '正在演奏' : '已停奏 · 可继续发送指令';
  if (previewPlaying()) playback.textContent += ' · 试听中，伴奏已静音';
  if (live.gaps > 0) playback.textContent += ` · 缓冲重接 ${live.gaps} 次`;
  byId('jam-bar').textContent = view.current_bar === null ? '—' : view.current_bar + 1;
  byId('jam-current-bpm').textContent = view.bpm;
  if (live.bpm !== view.bpm) {
    live.bpm = view.bpm;
    live.voice?.processor?.port.postMessage({bpm: view.bpm});
  }
  const layers = byId('jam-layers');
  const signature = JSON.stringify(view.layers);
  if (live.layerSignature !== signature) {
    live.layerSignature = signature;
    layers.replaceChildren(...view.layers.map(layer => {
      const item = document.createElement('li');
      const name = document.createElement('span');
      const state = document.createElement('span');
      item.dataset.muted = String(!layer.active);
      name.textContent = layer.label ?? layerNames[layer.name] ?? layer.name;
      state.textContent = layer.active ? `${Math.round(layer.density * 100)}% 密度` : '静音';
      item.append(name, state);
      return item;
    }));
  }
  const reply = view.feedback;
  if (reply) {
    const label = {buffered: '下一小节起拍后处理', thinking: '正在理解', arranging: view.renderer === 'mrt2' ? '正在生成 MRT2 试听，伴奏继续' : '正在编排',
      preview: 'MRT2 试听已就绪',
      scheduled: `将于第 ${reply.bar + 1} 小节生效`, applied: `已在第 ${reply.bar + 1} 小节生效`,
      waiting_ready: '仍在准备，原伴奏继续', expired: '保持当前演奏', held: '保持当前演奏'}[reply.status];
    const detail = reply.message === reply.text ? '' : reply.message;
    const text = [`「${reply.text}」`, label, detail].filter(Boolean).join(' · ');
    if (feedbackStatus.textContent !== text) feedbackStatus.textContent = text;
  }
  if (view.timbres && !live.timbreSending) {
    live.timbres = view.timbres;
    showTimbres(live);
  }
  if (view.preview_id && live.previewId !== view.preview_id) loadPreview(live, view.preview_id);
}

function clearPreview(live) {
  previewAudio.pause();
  previewAudio.removeAttribute('src');
  previewAudio.load();
  if (live.previewUrl) URL.revokeObjectURL(live.previewUrl);
  live.previewUrl = null;
  previewPanel.hidden = true;
}

async function loadPreview(live, id) {
  live.previewId = id;
  try {
    const result = await post('preview', {session: live.id, id}, 12000);
    if (run !== live || live.previewId !== id) return;
    clearPreview(live);
    const bytes = Uint8Array.from(atob(result.wav), character => character.charCodeAt(0));
    live.previewUrl = URL.createObjectURL(new Blob([bytes], {type: 'audio/wav'}));
    previewAudio.src = live.previewUrl;
    byId('jam-preview-download').href = live.previewUrl;
    previewPanel.hidden = false;
  } catch {
    if (run === live && live.previewId === id) live.previewId = null;
  }
}

async function enqueue(live, packet) {
  if (packet.bar <= live.buffered) return;
  const bytes = Uint8Array.from(atob(packet.wav), character => character.charCodeAt(0));
  const buffer = await live.clock.decodeAudioData(bytes.buffer);
  if (run !== live) return;
  const now = live.clock.currentTime;
  if (live.epoch === null) live.epoch = now + .12;
  let at = live.epoch + packet.start / live.rate;
  if (at < now + .025) {
    // A delayed tab or request can miss its write deadline. Continue from
    // the same musical bar using a new clock origin instead of skipping it.
    live.gaps += 1;
    at = now + .065;
    live.epoch = at - packet.start / live.rate;
  }
  const node = live.clock.createBufferSource();
  node.buffer = buffer;
  node.connect(live.output);
  node.onended = () => node.disconnect();
  node.start(at);
  live.markers.push({bar: packet.bar, at});
  live.buffered = packet.bar;
}

async function poll(live) {
  try {
    const now = live.clock.currentTime;
    while (live.markers.length > 1 && live.markers[1].at <= now) live.markers.shift();
    const current = live.markers[0];
    const started = current && current.at <= now ? current.bar : -1;
    const view = await post('poll', {
      session: live.id, started, buffered: live.buffered,
      sample: Math.max(0, Math.floor((now - live.epoch) * live.rate)),
    });
    if (run !== live) return;
    if (view.next) await enqueue(live, view.next);
    if (run !== live) return;
    live.failures = 0;
    show(view, live);
  } catch (error) {
    if (run !== live) return;
    if (++live.failures >= 3) {
      end(error.message || 'Jam 服务连接中断。');
      return;
    }
    playback.textContent = '正在重新连接…';
  }
  live.timer = setTimeout(() => poll(live), 100);
}

function releaseSession(id) {
  if (!id) return;
  fetch('/api/jam/stop', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session: id}), keepalive: true,
  }).catch(() => {});
}

function end(message = '会话已结束。') {
  const live = run;
  if (!live) return;
  run = null;
  clearTimeout(live.timer);
  releaseVoice(live);
  showTranscript(null);
  clearPreview(live);
  live.clock.close().catch(() => {});
  releaseSession(live.id);
  playback.textContent = '等待开始';
  byId('jam-bar').textContent = '—';
  byId('jam-layers').replaceChildren();
  voiceStatus.textContent = voiceAvailability();
  feedbackStatus.textContent = message;
  controls();
  showTimbres(null);
}

async function resumePlayback(clock) {
  let timer;
  try {
    await Promise.race([clock.resume(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('浏览器未能启动音频，请再点一次「开始演奏」。')), 4000);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

play.addEventListener('click', async () => {
  if (run || !bpmInput.reportValidity()) return;
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (!Audio) {
    feedbackStatus.textContent = '这个浏览器没有可用的音频播放接口。';
    return;
  }
  let clock;
  try {
    clock = new Audio();
  } catch {
    feedbackStatus.textContent = '浏览器无法打开音频输出。';
    return;
  }
  const live = {clock, id: null, rate: 0, epoch: null, buffered: -1,
    markers: [], gaps: 0, failures: 0, sending: false};
  live.output = live.clock.createGain();
  live.output.connect(live.clock.destination);
  run = live;
  controls();
  feedbackStatus.textContent = '正在启动伴奏…';
  try {
    await resumePlayback(live.clock);
    if (run !== live) return;
    const view = await post('start', {bpm: Number(bpmInput.value), agent: agent.value,
      renderer: renderer.value, arranger: arranger.value, profile: profile.value === 'custom' ? 'electric' : profile.value,
      timbres: renderer.value === 'fluidsynth' && timbreCatalog ? selectedTimbres() : null,
      allow_preview: renderer.value === 'mrt2'});
    if (run !== live) {
      releaseSession(view.session);
      return;
    }
    live.id = view.session;
    live.rate = view.sample_rate;
    await enqueue(live, view.next);
    if (run !== live) return;
    live.started = true;
    show(view, live);
    feedbackStatus.textContent = view.renderer === 'mrt2' ? '伴奏已启动。发送指令可生成 MRT2 试听。' : '可以发送第一条指令。';
    voiceStatus.textContent = voiceAvailability();
    controls();
    poll(live);
    if ((musicCapture || speechStatus === 'ready') && micSupported) enableVoice(live);
  } catch (error) {
    if (run === live) end(error.name === 'AbortError' ? '伴奏启动超时，请重试。' : (error.message || '无法开始演奏。'));
  }
});

stop.addEventListener('click', () => end());
async function sendToJam(live, text, source, voice = null, event = null) {
  if (!live?.id || (!text && !event)) return;
  if (live.sending) await live.sending;
  if (run !== live || (voice && live.voice !== voice)) return;
  live.sending = post('feedback', {session: live.id, ...(event ? {event} : {text, source})}).catch(error => ({error}));
  controls();
  const result = await live.sending;
  live.sending = null;
  if (run !== live) return;
  if (result.error) {
    feedbackStatus.textContent = result.error.message;
  } else {
    if (source === 'text' && input.value.trim() === text) input.value = '';
    feedbackStatus.textContent = result.feedback.status === 'buffered' ? '下一小节起拍后处理。' : '正在理解…';
  }
  controls();
}

form.addEventListener('submit', event => {
  event.preventDefault();
  sendToJam(run, input.value.trim(), 'text');
});

function voiceAvailability() {
  if (!micSupported) return '此浏览器无法录音，可以使用文字输入。';
  if (musicCapture) return run?.id ? '麦克风已关闭。' : '开始演奏后自动开启麦克风，首次使用需要授权。';
  if (speechStatus === 'checking') return '正在检查语音识别服务…';
  if (speechStatus === 'unsupported') return '当前工作台版本不支持语音识别，请打开新版工作台。';
  if (speechStatus === 'unreachable') return '暂时无法连接语音识别服务，可刷新页面重试或使用文字。';
  if (speechStatus === 'unconfigured') return '语音识别服务未配置，可以使用文字输入。';
  return run?.id ? '麦克风已关闭。' : '开始演奏后自动开启麦克风，首次使用需要授权。';
}

function previewPlaying() {
  return !previewAudio.paused && !previewAudio.ended && !previewAudio.error;
}

function setListening(live, listening) {
  const gain = live.output.gain;
  const now = live.clock.currentTime;
  const mute = previewPlaying();
  gain.cancelScheduledValues(now);
  gain.setTargetAtTime(mute ? 0 : listening ? .42 : 1, now, mute ? .015 : .04);
  if (mute) gain.setValueAtTime(0, now + .09);
}

function releaseVoice(live) {
  const voice = live.voice;
  if (!voice) return;
  live.voice = null;
  voice.source?.disconnect();
  voice.processor?.disconnect();
  voice.processor?.port.close();
  voice.stream?.getTracks().forEach(track => track.stop());
  setListening(live, false);
}

function encodeWave(samples, sampleRate) {
  const wave = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const writeTag = (offset, tag) => {
    for (const [i, letter] of [...tag].entries()) wave.setUint8(offset + i, letter.charCodeAt(0));
  };
  writeTag(0, 'RIFF');
  wave.setUint32(4, wave.byteLength - 8, true);
  writeTag(8, 'WAVE');
  writeTag(12, 'fmt ');
  wave.setUint32(16, 16, true);
  wave.setUint16(20, 1, true);
  wave.setUint16(22, 1, true);
  wave.setUint32(24, sampleRate, true);
  wave.setUint32(28, sampleRate * 2, true);
  wave.setUint16(32, 2, true);
  wave.setUint16(34, 16, true);
  writeTag(36, 'data');
  wave.setUint32(40, samples.length * 2, true);
  for (const [i, sample] of samples.entries()) {
    wave.setInt16(44 + i * 2, Math.max(-1, Math.min(1, sample)) * 32767, true);
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',', 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(new Blob([wave], {type: 'audio/wav'}));
  });
}

function showTranscript(text) {
  transcriptText.textContent = text ? `未自动发送 · 听到「${text}」` : '';
  transcriptPanel.hidden = !text;
}

useTranscript.addEventListener('click', () => {
  if (!run?.transcript) return;
  input.value = run.transcript;
  run.transcript = null;
  showTranscript(null);
  input.focus();
});

async function recognizeVoice(live, voice, segment) {
  const status = message => {
    if (run === live && live.voice === voice && !voice.listening) voiceStatus.textContent = message;
  };
  status('正在识别刚才的声音…');
  try {
    const audio = await encodeWave(segment.samples, segment.sampleRate);
    if (run !== live || live.voice !== voice) return;
    const route = musicCapture ? 'capture' : 'transcribe';
    const {command, event, transcript, recognition, speech_unavailable: speechUnavailable} = await post(route,
      {session: live.id, mime_type: 'audio/wav', audio, mode: segment.mode}, 28000);
    if (run !== live || live.voice !== voice) return;
    if (transcript) {
      live.transcript = command ? null : transcript;
      showTranscript(live.transcript);
    }
    if (event) {
      const detail = event.source === 'rhythm' ? `拍手 · ${event.payload.rhythm.onset_beats.length} 个拍点` :
        `哼唱 · ${event.payload.melody.notes.length} 个音符`;
      status(`正在监听 · 已识别${detail}`);
      await sendToJam(live, null, event.source, voice, event);
    } else if (command) {
      status(`正在监听 · 已识别「${transcript || command}」`);
      await sendToJam(live, command, 'speech', voice);
    } else {
      const messages = {
        no_speech: '语音转写没有得到文字，可以再说一次。',
        no_rhythm: '没有识别到拍手节奏。请跟着当前 BPM 拍一小节。',
        no_melody: '没有识别到哼唱旋律，可以再哼一小句。',
        unclassified: '这段声音没有识别成功。说话时可在高级设置选「只识别语音」。',
      };
      const detail = speechUnavailable ? '语音服务未配置，仍可拍手或哼唱。' :
        transcript ? '已转写。这句话未自动发送，可以编辑后发送。' :
          messages[recognition] || '这段声音没有识别成功。';
      status(`正在监听 · ${detail}`);
    }
  } catch (error) {
    status(error.name === 'AbortError' ? '正在监听 · 上一句识别超时。' : `正在监听 · ${error.message}`);
  }
}

function processVoiceQueue(live) {
  const voice = live.voice;
  if (!voice?.queue.length || live.voiceJob) return;
  live.voiceJob = recognizeVoice(live, voice, voice.queue.shift()).finally(() => {
    live.voiceJob = null;
    if (run === live) processVoiceQueue(live);
  });
}

async function enableVoice(live) {
  if (live.voice || !live.id) return;
  const voice = {queue: [], listening: false};
  live.voice = voice;
  controls();
  voiceStatus.textContent = '正在打开麦克风…';
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true},
    });
    if (run !== live || live.voice !== voice) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    voice.stream = stream;
    live.voiceModule ??= live.clock.audioWorklet.addModule('jam-voice-processor.js').catch(error => {
      live.voiceModule = null;
      throw error;
    });
    await live.voiceModule;
    if (run !== live || live.voice !== voice) return;
    voice.source = live.clock.createMediaStreamSource(stream);
    voice.processor = new AudioWorkletNode(live.clock, 'jam-voice', {channelCount: 1});
    voice.processor.port.postMessage({bpm: live.bpm});
    voice.processor.port.onmessage = ({data}) => {
      if (run !== live || live.voice !== voice) return;
      voice.listening = data.type === 'start';
      setListening(live, voice.listening);
      if (voice.listening) {
        voiceStatus.textContent = '正在听 · 伴奏已压低。';
      } else if (data.samples) {
        if (voice.queue.length < 2) {
          voice.queue.push({...data, mode: captureMode.value});
          if (live.voiceJob) voiceStatus.textContent = '正在监听 · 语音识别排队中。';
          processVoiceQueue(live);
        } else {
          voiceStatus.textContent = '正在监听 · 识别还在排队，请稍后再说。';
        }
      } else {
        voiceStatus.textContent = data.tooLong ? '正在监听 · 上一段太长，请把指令分开说。' : '正在监听。';
      }
    };
    const disableVoice = message => {
      if (run !== live || live.voice !== voice) return;
      releaseVoice(live);
      controls();
      voiceStatus.textContent = message;
    };
    voice.processor.onprocessorerror = () => disableVoice('麦克风处理已中断，可以重新开启。');
    stream.getAudioTracks().forEach(track => {
      track.onended = () => disableVoice('麦克风已断开，可以重新开启。');
    });
    // The processor outputs silence. Only the backing track reaches the speakers.
    voice.source.connect(voice.processor).connect(live.clock.destination);
    voiceStatus.textContent = '正在监听。';
  } catch (error) {
    if (run !== live || live.voice !== voice) return;
    releaseVoice(live);
    controls();
    voiceStatus.textContent = error.name === 'NotAllowedError' ? '请允许麦克风权限，然后点「开启麦克风」。' :
      '无法开启麦克风，可以重试或使用文字。';
  }
}

microphone.addEventListener('click', () => {
  const live = run;
  if (!live?.id) return;
  if (!live.voice) {
    enableVoice(live);
    return;
  }
  releaseVoice(live);
  controls();
  voiceStatus.textContent = voiceAvailability();
});

for (const button of examples) button.addEventListener('click', () => {
  input.value = button.dataset.example;
  form.requestSubmit();
});

renderer.addEventListener('change', () => {
  controls();
  showTimbres(run);
});
profile.addEventListener('change', () => {
  selectTimbres(timbreCatalog.presets[profile.value], profile.value);
  sendTimbres(run);
});
for (const select of Object.values(timbreSelects)) select.addEventListener('change', () => {
  profile.value = 'custom';
  sendTimbres(run);
});
for (const event of ['play', 'pause', 'ended', 'error']) previewAudio.addEventListener(event, () => {
  if (run) setListening(run, Boolean(run.voice?.listening));
});
bpmInput.addEventListener('input', () => { if (!run) byId('jam-current-bpm').textContent = bpmInput.value || '—'; });
window.addEventListener('pagehide', () => end());
post('status', {}).then(view => {
  agent.options[1].disabled = !view.llm_ready;
  arranger.options[1].disabled = !view.llm_ready;
  renderer.querySelector('option[value="fluidsynth"]').disabled = !view.fluidsynth;
  renderer.querySelector('option[value="mrt2"]').disabled = !view.mrt2;
  fillTimbres(view.timbre_catalog);
  captureMode.querySelector('option[value="speech"]').disabled = !view.speech_ready;
  musicCapture = Boolean(view.music_capture);
  mrtReady = Boolean(view.mrt2);
  speechStatus = view.speech_ready === false ? 'unconfigured' :
    view.speech_ready === true && view.speech_mode === 'continuous' ? 'ready' : 'unsupported';
  voiceStatus.textContent = voiceAvailability();
  controls();
  if (run?.id && (musicCapture || speechStatus === 'ready') && micSupported) enableVoice(run);
}).catch(() => {
  speechStatus = 'unreachable';
  voiceStatus.textContent = voiceAvailability();
  controls();
});
