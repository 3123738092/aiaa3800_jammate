import {MRT2_RATE, readMrt2Bar, stereoPcmBuffer} from './jam-mrt2-stream.js';
import {cancelHandoff, scheduleHandoff} from './jam-renderer-audio.js';
import {pcmWave, StreamRecording, waveHeader} from './jam-recording.js';
import {initJamDebug} from './jam-debug.js?v=studio-v4';
import {initJamInputs} from './jam-inputs.js?v=studio-v4';
import {analyzeJamHumming} from './jam-humming.js';
import {addressedCommand, initNodConfirmation} from './jam-nod.js?v=2';

const MRT2_START_BUFFER_FRAMES = Math.round(.8 * MRT2_RATE);
const MRT2_SCHEDULE_AHEAD_S = .32;

const byId = id => document.getElementById(id);
const play = byId('jam-start'), stop = byId('jam-stop');
const completeHumming = byId('jam-complete-humming');
const hummingCompletionNote = byId('jam-humming-completion-note');
const exportButton = byId('jam-export'), exportStatus = byId('jam-export-status');
const agent = byId('jam-agent'), bpmInput = byId('jam-bpm');
const openingMode = byId('jam-opening-mode'), openingModeNote = byId('jam-opening-mode-note');
const correctHumming = byId('jam-correct-humming'), correctionNote = byId('jam-correct-humming-note');
let hummingCorrectionReady = false;
const coldSettings = byId('jam-cold-settings'), coldBpm = byId('jam-cold-bpm'), coldKey = byId('jam-cold-key');
const warmBpmRow = byId('jam-warm-bpm');
let coldSettingsAvailable = false, coldMrtReady = false, mrtLiveState = false;
const arranger = byId('jam-arranger'), renderer = byId('jam-renderer'), profile = byId('jam-profile');
const timbrePanel = byId('jam-timbres'), timbreStatus = byId('jam-timbre-status');
const timbreOverride = byId('jam-override-timbres'), captureMode = byId('jam-input-mode');
const timbreSelects = {keys: byId('jam-keys-program'), bass: byId('jam-bass-program')};
const previewAudio = byId('jam-preview-audio'), previewPanel = byId('jam-preview-output');
const autoPreview = byId('jam-mrt2-auto'), autoPreviewRow = byId('jam-mrt2-auto-row');
const audioReference = byId('jam-mrt2-reference'), rhythmTiming = byId('jam-rhythm-timing');
const backendStatus = byId('jam-backend-status');
const form = byId('jam-feedback'), input = byId('jam-text'), send = byId('jam-send');
const playback = byId('jam-playback'), feedbackStatus = byId('jam-feedback-status');
const microphone = byId('jam-microphone'), voiceStatus = byId('jam-voice-status');
const captionsToggle = byId('jam-captions-toggle'), captionsPanel = byId('jam-captions');
const captionsStatus = byId('jam-captions-status'), captionsText = byId('jam-captions-text');
const useCaption = byId('jam-captions-use');
const transcriptPanel = byId('jam-transcript'), transcriptText = byId('jam-transcript-text');
const useTranscript = byId('jam-transcript-use');
const examples = [...form.querySelectorAll('[data-example]')];
const layerNames = {drums: '鼓', bass: '贝斯', keys: '键盘', lead: '主奏', guitar: '木吉他',
  flute: '长笛', strings: '弦乐', harp: '竖琴'};
const micSupported = Boolean(navigator.mediaDevices?.getUserMedia && window.AudioWorkletNode);
let speechStatus = 'checking';
let openingReady = false;
let hummingCompletionReady = false;
let coldAvailable = false;
let openingChecked = false;
let musicCaptureAvailable = false;
let hummingSeconds = 0;
let rhythmOpeningSeconds = 18;
let mrtReady = false, mrt2StreamReady = false, mrt2DirectReady = false;
let timbreCatalog = null;
let run = null;
let lastPerformance = null;
let exporting = false;

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

const debug = initJamDebug(post, (name, timbres) => {
  const program = timbres?.active?.[name] ?? (name.startsWith('gm_') ? Number(name.slice(3)) : null);
  const instrument = layerNames[name];
  const timbre = timbreCatalog?.programs?.[program];
  return instrument && timbre ? instrument + ' · ' + timbre : instrument ?? timbre ?? name;
});
const nod = initNodConfirmation({reportError: debug.error});
let pausedVoice = null;
const manual = initJamInputs({
  post, encodeWave, waveBlob, liveSession: () => run?.started ? run : null, onChange: controls,
  correctHumming: () => hummingCorrectionReady && correctHumming.checked,
  rhythmTiming: () => rhythmTiming.value,
  sendEvent: (live, event) => sendToJam(live, null, event.source, null, event),
  onRecord(recording) {
    if (recording) {
      pausedVoice = run?.voice ? run : null;
      if (pausedVoice) releaseVoice(pausedVoice);
      if (run?.started) setListening(run, true);
    } else {
      if (run?.started) setListening(run, false);
      if (pausedVoice && pausedVoice === run) enableVoice(pausedVoice);
      pausedVoice = null;
    }
  },
});

function controls() {
  updateExport();
  const cold = openingMode.value === 'cold';
  const canOpen = cold ? coldAvailable : openingReady;
  const useColdSettings = cold && coldSettingsAvailable;
  coldSettings.hidden = !useColdSettings || Boolean(run?.started);
  warmBpmRow.hidden = useColdSettings;
  for (const control of [coldBpm, coldKey]) control.disabled = Boolean(run);
  openingMode.disabled = Boolean(run);
  openingMode.querySelector('option[value="cold"]').disabled = !coldAvailable;
  openingModeNote.textContent = cold
    ? '按已提供素材演奏。只有哼唱时复奏一次。可以指定和弦进行来加入伴奏。'
    : '明确指定的独奏和纯鼓开场会保持单声部。开放式开场允许补全，MRT2 独立生成会直接使用音乐风格条件。';
  const mrtOption = renderer.querySelector('option[value="mrt2"]');
  mrtOption.disabled = !mrtReady || (cold && !coldMrtReady);
  const streamOption = renderer.querySelector('option[value="mrt2_stream"]');
  streamOption.disabled = !mrt2StreamReady;
  const directOption = renderer.querySelector('option[value="mrt2_direct"]');
  directOption.disabled = !mrt2DirectReady;
  if ((mrtOption.disabled && renderer.value === 'mrt2')
      || (streamOption.disabled && renderer.value === 'mrt2_stream')
      || (directOption.disabled && renderer.value === 'mrt2_direct')) renderer.value = 'synth';
  for (const control of [bpmInput, agent, arranger, renderer]) control.disabled = Boolean(run);
  const mrt = renderer.value === 'mrt2', stream = renderer.value === 'mrt2_stream';
  const direct = renderer.value === 'mrt2_direct';
  byId('jam-mrt2-reference-row').hidden = !direct;
  audioReference.disabled = Boolean(run) || !direct;
  arranger.closest('label').hidden = direct;
  arranger.disabled = Boolean(run) || direct;
  const starting = Boolean(run) && !run.started;
  correctHumming.disabled = starting || !hummingCorrectionReady;
  correctionNote.textContent = hummingCorrectionReady ? '把识别出的音符交给模型，尝试修正跑音或剔除误识别的音。' :
    openingChecked ? '矫正模型不可用，仍可使用基础清理。' : '正在检查矫正模型…';
  play.disabled = Boolean(run) || !canOpen || !manual.ready(useColdSettings && Boolean(coldBpm.value || coldKey.value));
  play.textContent = starting ? '正在识别与编制…' : '分析素材并开始';
  play.setAttribute('aria-busy', String(starting));
  completeHumming.hidden = !hummingCompletionReady || Boolean(run?.started);
  hummingCompletionNote.hidden = completeHumming.hidden;
  completeHumming.disabled = Boolean(run) || !hummingCompletionReady || !manual.hasHumming() || !manual.ready();
  const completing = starting && run.mode === 'humming';
  completeHumming.textContent = completing ? '正在补全哼唱…' : '自动补全哼唱';
  completeHumming.setAttribute('aria-busy', String(completing));
  stop.disabled = !run;
  timbrePanel.hidden = renderer.value !== 'fluidsynth' || !timbreCatalog;
  byId('jam-override-timbres-row').hidden = Boolean(run?.started);
  timbreOverride.disabled = starting;
  for (const control of [profile, ...Object.values(timbreSelects)]) {
    control.disabled = !timbreCatalog || starting || (!run?.started && !timbreOverride.checked);
  }
  const mrtHint = byId('jam-mrt2-hint');
  mrtHint.hidden = !mrt && !stream && !direct;
  autoPreviewRow.hidden = !mrt || !mrtLiveState;
  if (!run?.started) {
    mrtHint.textContent = direct
      ? 'MRT2 根据 BandPlan 独立生成并流式播放。速度、调性和声部是生成目标。'
      : stream
      ? '从本机 MRT2 模型边生成边播放。指令按 JamMate 的小节时钟更新生成条件。'
      : cold ? '开始后按现有素材生成一段独立试听。' : '发送指令后，MRT2 会在下方生成独立试听。';
  }
  backendStatus.textContent = mrtReady ? '' : 'MRT2 在另一个服务中可用。';
  if (!mrtReady && ['4193', '4195'].includes(location.port) && !mrt) {
    const link = document.createElement('a');
    link.href = 'http://127.0.0.1:4196/jam.html';
    link.textContent = '打开 MRT2 工作台';
    backendStatus.replaceChildren(link);
  }
  const canSend = Boolean(run?.id) && !run.sending;
  for (const control of [input, send, useTranscript, ...examples]) control.disabled = !canSend;
  microphone.disabled = !run?.id || !micSupported || !voiceEnabled();
  microphone.textContent = run?.voice ? '关闭麦克风' : '开启麦克风';
  microphone.setAttribute('aria-pressed', String(Boolean(run?.voice)));
  const captions = run?.voice?.captions;
  nod.setAvailable(Boolean(captions));
  captionsToggle.disabled = speechStatus !== 'ready' || !run?.voice?.processor || !usesSpeech();
  if (speechStatus !== 'ready') captionsToggle.title = '当前服务未配置语音转写';
  else if (!usesSpeech()) captionsToggle.title = '在高级设置中选择语音或自动识别后可开启转录';
  else if (!run?.voice?.processor) captionsToggle.title = '开始演奏并打开麦克风后可开启字幕';
  else captionsToggle.title = '停顿后由语音服务转写这一句';
  captionsToggle.textContent = captions ? '暂停语音转录' : '开启语音转录';
  captionsToggle.setAttribute('aria-pressed', String(Boolean(captions)));
  useCaption.disabled = !canSend || !captions?.final;
  manual.update({starting, ready: canOpen, checked: openingChecked, cold, hummingSeconds, rhythmOpeningSeconds,
    hummingCompletion: hummingCompletionReady});
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
  let text = `当前 SoundFont 有 ${Object.keys(timbreCatalog.programs).length} 种音色。演奏中可按小节切换。`;
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
  window.dispatchEvent(new CustomEvent('jammate:view', {detail: view}));
  window.dispatchEvent(new CustomEvent('jammate:playback-metrics', {detail: {
    session: live.id, gaps: live.gaps, failures: live.failures,
    bar: view.current_bar, contextState: live.clock.state,
    boundaryGaps: live.boundaryGaps, withinBarGaps: live.gaps - live.boundaryGaps,
  }}));
  if (view.current_bar !== null && !live.exportReady) {
    live.exportReady = true;
    updateExport();
  }
  playback.textContent = view.current_bar === null ? '准备播放' : view.playing ? '正在演奏' : '已停奏 · 可继续发送指令';
  if (previewPlaying()) playback.textContent += ' · 手动试听';
  else if (live.autoClip?.at <= live.clock.currentTime) playback.textContent += ' · MRT2 片段已开始播放';
  if (live.gaps > 0) playback.textContent += ` · 缓冲重接 ${live.gaps} 次`;
  byId('jam-bar').textContent = view.current_bar === null ? '—' : view.current_bar + 1;
  byId('jam-current-bpm').textContent = Math.round(view.bpm * 10) / 10;
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
  if (live.mrt2Stream) {
    const hint = byId('jam-mrt2-hint');
    hint.textContent = view.current_bar === null
      ? '本地 MRT2 正在准备首帧…'
      : live.directMrt2 ? 'MRT2 独立生成中 · 页面显示 BandPlan 的生成目标'
        : '本地 MRT2 流式播放 · 声部和小节标记对应生成条件';
  }
  const reply = view.feedback;
  if (reply) {
    const label = {buffered: '下一小节起拍后处理', thinking: '正在理解', arranging: view.renderer === 'mrt2' ? '正在生成 MRT2 试听，伴奏继续' : '正在编排',
      preview: 'MRT2 试听已就绪',
      scheduled: `将于第 ${reply.bar + 1} 小节生效`, applied: live.directMrt2
        ? `第 ${reply.bar + 1} 小节已将计划送入 MRT2` : `已在第 ${reply.bar + 1} 小节生效`,
      waiting_ready: '仍在准备，原伴奏继续', expired: '保持当前演奏', held: '保持当前演奏'}[reply.status];
    const detail = reply.message === reply.text ? '' : reply.message;
    const text = [`「${reply.text}」`, label, detail].filter(Boolean).join(' · ');
    if (feedbackStatus.textContent !== text) feedbackStatus.textContent = text;
  }
  if (view.timbres && !live.timbreSending) {
    if (!live.timbres) selectTimbres(view.timbres.active);
    live.timbres = view.timbres;
    showTimbres(live);
  }
  const openingPreview = view.opening_preview;
  if (openingPreview) {
    const label = {
      generating: 'MRT2 正在生成开场试听…',
      completed: previewPlaybackLabel(live, view, 'opening_' + live.id),
      waiting_material: '等待可演奏素材。仅设置速度或调性时没有开场音频。',
      failed: 'MRT2 开场试听失败 · ' + (openingPreview.error ?? ''),
    }[openingPreview.status];
    const hint = byId('jam-mrt2-hint');
    if (label && hint.textContent !== label) hint.textContent = label;
  }
  const preview = view.preview_status;
  if (preview) {
    const label = {generating: '实时伴奏已接收计划，MRT2 正在生成…',
      completed: previewPlaybackLabel(live, view, preview.event),
      failed: 'MRT2 生成失败，实时伴奏继续 · ' + (preview.error ?? '')}[preview.status];
    const hint = byId('jam-mrt2-hint');
    if (label && hint.textContent !== label) hint.textContent = label;
  }
  live.autoWanted = live.sending ? null : view.auto_preview_id ?? null;
  if (live.autoClip && (!view.playing || (live.autoClip.id !== live.autoWanted
      && live.autoClip.at > live.clock.currentTime))) stopAutoPreview(live);
  if (view.feedback?.status === 'applied' && view.feedback.keeps_material === false
      && live.autoClip && live.autoClip.id !== view.feedback.id) stopAutoPreview(live);
  if (!view.playing && previewPlaying()) previewAudio.pause();
  if (view.preview_id && live.previewId !== view.preview_id) loadPreview(live, view.preview_id);
  scheduleAutoPreview(live);
  const playingAuto = live.autoClip && live.autoClip.at <= live.clock.currentTime;
  debug.setPlayback({source: previewPlaying() ? 'MRT2 手动试听' : playingAuto ? 'MRT2 片段接管' :
    view.current_bar == null ? '等待起拍' : !view.playing ? '演奏已停止' :
    live.mrt2Stream ? 'MRT2 流式 PCM' : '实时伴奏',
    preview_id: previewPlaying() ? live.previewId : playingAuto ? live.autoClip.id : null,
    bar: view.current_bar, buffer_reconnections: live.gaps,
    bar_boundary_reconnections: live.boundaryGaps, within_bar_reconnections: live.gaps - live.boundaryGaps});
  debug.refresh();
}

function previewPlaybackLabel(live, view, id) {
  const clip = live.autoClip;
  if (clip?.id === id) {
    return clip.at <= live.clock.currentTime ? 'MRT2 片段已开始播放。' : 'MRT2 片段已排程，等待小节起拍。';
  }
  if (live.autoPlayed === id) return view.playing ? 'MRT2 片段已播放，实时伴奏继续。' : 'MRT2 片段已播放。';
  if (autoPreview.checked && mrtLiveState && view.auto_preview_id === id) return 'MRT2 片段就绪，等待接入。';
  return 'MRT2 片段就绪，可以试听。';
}

function stopAutoPreview(live, pendingOnly = false) {
  const clip = live.autoClip;
  const now = live.clock.currentTime;
  if (!clip || (pendingOnly && clip.at <= now)) return;
  live.autoClip = null;
  if (clip.at <= now) live.autoPlayed = clip.id;
  clip.record.end = Math.min(clip.record.end, Math.max(clip.at, now));
  cancelHandoff(live.baselineGate.gain, clip.gate.gain, clip.at, clip.end, now);
  clip.node.stop(now);
}

function scheduleAutoPreview(live) {
  const ready = live.autoReady;
  if (live.directMrt2 || !autoPreview.checked || previewPlaying() || live.sending || !ready ||
      ready.id !== live.autoWanted || ready.id === live.autoPlayed || live.autoClip) return;
  const now = live.clock.currentTime;
  const boundary = live.markers.find(marker => marker.at >= now + .07);
  if (!boundary) return;
  const at = boundary.at, end = at + ready.buffer.duration;
  const node = live.clock.createBufferSource();
  const gate = live.clock.createGain();
  node.buffer = ready.buffer;
  node.connect(gate).connect(live.autoOutput);
  const record = {at, end, wav: ready.wav};
  live.autoRecorded.push(record);
  live.autoClip = {id: ready.id, at, end, node, gate, record};
  scheduleHandoff(live.baselineGate.gain, gate.gain, at, end, now);
  node.onended = () => {
    node.disconnect();
    gate.disconnect();
    if (live.autoClip?.node === node) {
      live.autoPlayed = ready.id;
      live.autoClip = null;
    }
  };
  node.start(at);
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
    if (mrtLiveState) {
      const buffer = await live.clock.decodeAudioData(bytes.buffer);
      if (run !== live || live.previewId !== id) return;
      live.autoReady = {id, buffer, wav: result.wav};
      scheduleAutoPreview(live);
    }
  } catch (error) {
    if (run === live && live.previewId === id) {
      live.previewId = null;
      debug.error('MRT2 试听下载', error);
    }
  }
}

function failMrt2Stream(live, error) {
  if (run !== live) return;
  debug.error('MRT2 流式播放', error);
  end(error.message || '本地 MRT2 音频流已中断。');
}

function scheduleMrt2Audio(live) {
  const stream = live.mrt2Stream;
  if (run !== live || stream.scheduleTimer !== null || !stream.ready.length) return;
  const head = stream.ready[0];
  if (live.epoch === null) {
    const tail = stream.ready.at(-1);
    const receivedFrames = tail.offset + tail.pcm.byteLength / (2 * Int16Array.BYTES_PER_ELEMENT);
    if (receivedFrames < Math.min(MRT2_START_BUFFER_FRAMES, head.packet.pcm_frames)) return;
    live.epoch = live.clock.currentTime + .05 - head.packet.start / live.rate;
  }

  try {
    while (stream.ready.length) {
      const {packet, pcm, offset} = stream.ready[0];
      const planned = packet.start / live.rate + offset / MRT2_RATE;
      const now = live.clock.currentTime;
      let at = live.epoch + planned;
      if (at - now > MRT2_SCHEDULE_AHEAD_S) {
        stream.scheduleTimer = setTimeout(() => {
          stream.scheduleTimer = null;
          scheduleMrt2Audio(live);
        }, 20);
        return;
      }
      if (at < now + .008) {
        live.gaps += 1;
        if (offset === 0) live.boundaryGaps += 1;
        at = now + .05;
        live.epoch = at - planned;
      }
      stream.ready.shift();
      const node = live.clock.createBufferSource();
      node.buffer = stereoPcmBuffer(live.clock, pcm);
      node.connect(live.output);
      node.onended = () => node.disconnect();
      node.start(at);
      stream.end = at + node.buffer.duration;
      if (offset === 0) live.markers.push({bar: packet.bar, at});
      live.streamRecording.append(packet.bar, at, pcm);
    }
  } catch (error) {
    failMrt2Stream(live, error);
  }
}

function queueMrt2Bar(live, packet) {
  const stream = live.mrt2Stream;
  live.buffered = packet.bar;
  // The server offers one reserved bar. Let its bytes arrive independently
  // of the short Web Audio queue while keeping model requests in bar order.
  stream.pending = stream.pending.then(() => readMrt2Bar(live.id, packet, stream.controller.signal,
    (pcm, offset) => {
      if (run !== live) return;
      stream.ready.push({packet, pcm, offset});
      scheduleMrt2Audio(live);
    })).catch(error => failMrt2Stream(live, error));
}

async function enqueue(live, packet) {
  if (packet.bar <= live.buffered) return;
  if (live.mrt2Stream) {
    queueMrt2Bar(live, packet);
    return;
  }
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
    live.boundaryGaps += 1;
    stopAutoPreview(live);
    at = now + .065;
    live.epoch = at - packet.start / live.rate;
  }
  const node = live.clock.createBufferSource();
  node.buffer = buffer;
  node.connect(live.output);
  node.onended = () => node.disconnect();
  node.start(at);
  live.recorded.push({at, end: at + buffer.duration, wav: packet.wav});
  live.markers.push({bar: packet.bar, at});
  live.buffered = packet.bar;
}

async function poll(live) {
  try {
    const now = live.clock.currentTime;
    while (live.markers.length > 1 && live.markers[1].at <= now) live.markers.shift();
    const current = live.markers[0];
    const started = current && current.at <= now ? current.bar : -1;
    const playbackTime = live.mrt2Stream ? Math.min(now, live.mrt2Stream.end) : now;
    const view = await post('poll', {
      session: live.id, started, buffered: live.buffered,
      sample: live.epoch === null ? 0 : Math.max(0, Math.floor((playbackTime - live.epoch) * live.rate)),
    });
    if (run !== live) return;
    if (view.next) await enqueue(live, view.next);
    if (run !== live) return;
    live.failures = 0;
    show(view, live);
  } catch (error) {
    if (run !== live) return;
    debug.error('播放同步', error);
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
  stopAutoPreview(live);
  lastPerformance = performanceSnapshot(live) ?? lastPerformance;
  run = null;
  if (live.mrt2Stream) {
    clearTimeout(live.mrt2Stream.scheduleTimer);
    live.mrt2Stream.controller.abort();
    live.mrt2Stream.ready.length = 0;
  }
  manual.stop(live.started);
  debug.stop();
  clearTimeout(live.timer);
  releaseVoice(live);
  showTranscript(null);
  clearPreview(live);
  live.clock.close().catch(() => {});
  releaseSession(live.id);
  playback.textContent = '等待开始';
  byId('jam-bar').textContent = '—';
  byId('jam-current-bpm').textContent = '—';
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

play.addEventListener('click', () => startJam(openingMode.value));
completeHumming.addEventListener('click', () => startJam('humming'));

async function startJam(mode) {
  const canOpen = mode === 'cold' ? coldAvailable : mode === 'humming' ? hummingCompletionReady : openingReady;
  const useColdSettings = openingMode.value === 'cold' && coldSettingsAvailable;
  const tempoInput = useColdSettings ? coldBpm : bpmInput;
  const key = useColdSettings ? coldKey.value : '';
  const hasSettings = useColdSettings && Boolean(tempoInput.value || key);
  if (run || !canOpen || !manual.ready(hasSettings) || !tempoInput.reportValidity()) return;
  if (mode === 'humming' && !manual.hasHumming()) return;
  const settings = {bpm: tempoInput.value ? Number(tempoInput.value) : null,
    timbres: renderer.value === 'fluidsynth' && timbreOverride.checked ? selectedTimbres() : null};
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
  const live = {clock, mode, id: null, rate: 0, epoch: null, buffered: -1,
    markers: [], recorded: [], autoRecorded: [], gaps: 0, boundaryGaps: 0, failures: 0, sending: false,
    directMrt2: renderer.value === 'mrt2_direct'};
  if (live.directMrt2 || renderer.value === 'mrt2_stream') {
    live.mrt2Stream = {
      controller: new AbortController(), pending: Promise.resolve(), ready: [], scheduleTimer: null, end: 0,
    };
    live.streamRecording = new StreamRecording();
  }
  live.output = live.clock.createGain();
  if (live.directMrt2) {
    live.output.connect(live.clock.destination);
  } else {
    live.baselineGate = live.clock.createGain();
    live.autoOutput = live.clock.createGain();
    live.output.connect(live.baselineGate).connect(live.clock.destination);
    live.autoOutput.connect(live.clock.destination);
  }
  window.dispatchEvent(new CustomEvent('jammate:audio', {detail: {
    context: live.clock, output: live.baselineGate ?? live.output, autoOutput: live.autoOutput,
  }}));
  run = live;
  debug.start(null);
  controls();
  feedbackStatus.textContent = '正在启动伴奏…';
  try {
    await resumePlayback(live.clock);
    if (run !== live) return;
    const opening = await manual.request();
    if (run !== live) return;
    opening.rhythm_timing = rhythmTiming.value;
    if (key) opening.key = key;
    if (hasSettings && !opening.text && !opening.rhythm && !opening.humming) {
      opening.text = [settings.bpm && settings.bpm + ' BPM', key].filter(Boolean).join('，');
    }
    const view = await post('start', {opening, opening_mode: mode, ...settings, agent: agent.value, renderer: renderer.value,
      arranger: live.directMrt2 ? 'rule' : arranger.value, allow_preview: renderer.value === 'mrt2',
      mrt2_audio_reference: live.directMrt2 && audioReference.checked}, 85000);
    if (run !== live) {
      releaseSession(view.session);
      return;
    }
    live.id = view.session;
    if (!view.opening) throw new Error('当前服务版本需要更新，才能用拍手和哼唱开始 Jam。');
    debug.start(live.id);
    live.rate = view.sample_rate;
    await enqueue(live, view.next);
    if (run !== live) return;
    live.started = true;
    lastPerformance = null;
    exportStatus.textContent = '';
    manual.opened(view.opening, view.bpm);
    show(view, live);
    feedbackStatus.textContent = view.renderer === 'mrt2' && mode === 'warm'
      ? '伴奏已启动。发送指令可生成 MRT2 试听。' : '可以发送第一条指令。';
    voiceStatus.textContent = voiceAvailability();
    controls();
    poll(live);
    if (voiceEnabled() && micSupported) enableVoice(live);
  } catch (error) {
    if (run === live) {
      debug.error('启动会话', error);
      const message = error.name === 'AbortError' ? '开场分析超时，请重试。' : (error.message || '无法开始演奏。');
      end(message);
      byId('jam-opening-status').textContent = message;
    }
  }
}

stop.addEventListener('click', () => end());
async function sendToJam(live, text, source, voice = null, event = null) {
  if (text && source === 'speech' && !/鼓|键盘|钢琴|贝斯|主奏|drum|piano|keys|bass/i.test(text)) {
    const target = document.querySelector('main.studio')?.dataset.target;
    if (target === 'drums') text = '鼓' + text;
    else if (target === 'keys') text = '键盘' + text;
  }
  if (!live?.id || (!text && !event)) return;
  if (live.sending) await live.sending;
  if (run !== live || (voice && live.voice !== voice)) return;
  stopAutoPreview(live, true);
  live.autoWanted = null;
  live.sending = post('feedback', {session: live.id, ...(event ? {event} : {text, source})}).catch(error => ({error}));
  controls();
  const result = await live.sending;
  live.sending = null;
  if (run !== live) return;
  if (result.error) {
    debug.error('发送指令', result.error);
    feedbackStatus.textContent = result.error.message;
  } else {
    if (source === 'text' && input.value.trim() === text) input.value = '';
    window.dispatchEvent(new CustomEvent('jammate:submitted', {detail: {source, text}}));
    feedbackStatus.textContent = result.feedback.status === 'buffered' ? '下一小节起拍后处理。' : '正在理解…';
  }
  controls();
  return !result.error;
}

form.addEventListener('submit', event => {
  event.preventDefault();
  sendToJam(run, input.value.trim(), 'text');
});

function usesSpeech() {
  return captureMode.value === 'speech' || captureMode.value === 'auto';
}

function voiceEnabled() {
  return (usesSpeech() && speechStatus === 'ready') ||
    (captureMode.value !== 'speech' && musicCaptureAvailable);
}

function voiceAvailability() {
  if (!micSupported) return '此浏览器无法录音，可以使用文字输入。';
  if (captureMode.value !== 'speech' && musicCaptureAvailable) {
    return run?.id ? '麦克风已关闭。' : '开始后会按高级设置中的模式识别麦克风。';
  }
  if (speechStatus === 'checking') return '正在检查语音识别服务…';
  if (speechStatus === 'unsupported') return '当前服务未配置语音转写，可以使用文字。';
  if (speechStatus === 'unreachable') return '暂时无法连接语音识别服务，可刷新页面重试或使用文字。';
  return run?.id ? '麦克风已关闭。' : '开始演奏后自动开启麦克风，首次使用需要授权。';
}

function previewPlaying() {
  return !previewAudio.paused && !previewAudio.ended && !previewAudio.error;
}

function setListening(live, listening) {
  const gain = live.output.gain;
  const now = live.clock.currentTime;
  const mute = !live.directMrt2 && previewPlaying();
  live.autoOutput?.gain.setTargetAtTime(listening ? .42 : 1, now, .04);
  gain.cancelScheduledValues(now);
  if (mute) gain.setValueAtTime(0, now);
  else gain.setTargetAtTime(listening ? .42 : 1, now, .04);
}

function releaseVoice(live) {
  const voice = live.voice;
  if (!voice) return;
  live.voice = null;
  stopCaptions(voice);
  voice.source?.disconnect();
  voice.processor?.disconnect();
  voice.processor?.port.close();
  voice.stream?.getTracks().forEach(track => track.stop());
  setListening(live, false);
}

function stopCaptions(voice) {
  voice.captions = null;
  nod.setAvailable(false);
  captionsText.textContent = '';
  captionsStatus.textContent = '';
  captionsPanel.hidden = true;
}

function startCaptions(live, voice) {
  if (run !== live || !usesSpeech() || speechStatus !== 'ready' || voice.captions) return;
  voice.captions = {final: '', reviewRevision: 0};
  captionsPanel.hidden = false;
  captionsText.textContent = '';
  captionsStatus.textContent = '· 等待说话';
  controls();
}

async function handleTranscript(live, voice, segment, result) {
  const {captions, reviewRevision} = segment;
  if (!captions || voice.captions !== captions) return;
  const transcript = result.transcript;
  captions.final = transcript;
  captionsText.textContent = transcript;
  captionsStatus.textContent = '· 最近一句';

  const review = nod.enabled();
  if (review && captions.reviewRevision !== reviewRevision) return;
  const command = transcript.length <= input.maxLength &&
    ((review && addressedCommand(transcript)) || result.command);
  live.transcript = command ? null : transcript;
  showTranscript(live.transcript);
  if (command && review) {
    if (!nod.offer({transcript, send: () => run === live && live.voice === voice &&
        voice.captions === captions && captions.reviewRevision === reviewRevision &&
        sendToJam(live, command, 'speech', voice)})) {
      live.transcript = transcript;
      showTranscript(transcript);
    }
  } else if (command) {
    await sendToJam(live, command, 'speech', voice);
  }
  controls();
}

captionsToggle.addEventListener('click', () => {
  const voice = run?.voice;
  if (captionsToggle.disabled || !voice) return;
  if (voice.captions) {
    stopCaptions(voice);
    controls();
  } else {
    startCaptions(run, voice);
  }
});

useCaption.addEventListener('click', () => {
  const captions = run?.voice?.captions;
  if (useCaption.disabled || !captions?.final) return;
  input.value = captions.final.slice(0, input.maxLength);
  input.focus();
});

function waveBlob(samples, sampleRate, right = null) {
  const channels = right ? 2 : 1;
  const dataBytes = samples.length * channels * 2;
  const wave = new DataView(new ArrayBuffer(dataBytes));
  for (let i = 0; i < samples.length; i++) {
    const offset = i * channels * 2;
    wave.setInt16(offset, Math.max(-1, Math.min(1, samples[i])) * 32767, true);
    if (right) wave.setInt16(offset + 2, Math.max(-1, Math.min(1, right[i])) * 32767, true);
  }
  return new Blob([waveHeader(sampleRate, channels, samples.length), wave], {type: 'audio/wav'});
}

function encodeWave(samples, sampleRate) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',', 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(waveBlob(samples, sampleRate));
  });
}

function performanceSnapshot(live) {
  if (live?.streamRecording) return live.streamRecording.snapshot(live.clock.currentTime);
  if (!live?.recorded.length) return null;
  const now = live.clock.currentTime;
  const bars = live.recorded.filter(bar => bar.at < now);
  if (!bars.length) return null;
  const start = bars[0].at;
  const end = Math.min(now, bars.reduce((last, bar) => Math.max(last, bar.end), start));
  const clips = live.autoRecorded.filter(clip => clip.at < end && clip.end > clip.at)
    .map(clip => ({...clip, end: Math.min(clip.end, end)}));
  return {rate: live.rate, bars, clips, start, end};
}

function updateExport() {
  exportButton.disabled = exporting || !((run ? run.exportReady : lastPerformance) &&
    (run?.streamRecording || lastPerformance?.pcm || window.OfflineAudioContext || window.webkitOfflineAudioContext));
}

async function performanceWave(take, length) {
  if (take.pcm) return pcmWave(take);
  const OfflineAudio = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const mix = new OfflineAudio(1, length, take.rate);
  const baseline = mix.createGain();
  baseline.connect(mix.destination);
  for (const bar of take.bars) {
    const source = mix.createBufferSource();
    const bytes = Uint8Array.from(atob(bar.wav), character => character.charCodeAt(0));
    source.buffer = await mix.decodeAudioData(bytes.buffer);
    source.connect(baseline);
    source.start(bar.at - take.start);
  }
  for (const clip of take.clips) {
    const at = clip.at - take.start, duration = clip.end - clip.at;
    const source = mix.createBufferSource();
    const gate = mix.createGain();
    const bytes = Uint8Array.from(atob(clip.wav), character => character.charCodeAt(0));
    source.buffer = await mix.decodeAudioData(bytes.buffer);
    source.connect(gate).connect(mix.destination);
    scheduleHandoff(baseline.gain, gate.gain, at, at + duration, 0);
    source.start(at, 0, duration);
  }
  const result = await mix.startRendering();
  return waveBlob(result.getChannelData(0), result.sampleRate);
}

exportButton.addEventListener('click', async () => {
  const take = run ? performanceSnapshot(run) : lastPerformance;
  if (exporting || !take) return;
  exporting = true;
  updateExport();
  exportStatus.textContent = '正在合成 WAV…';
  try {
    const length = Math.max(1, Math.round((take.end - take.start) * take.rate));
    const url = URL.createObjectURL(await performanceWave(take, length));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'jammate-performance.wav';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    exportStatus.textContent = '已导出 ' + (length / take.rate).toFixed(1) + ' 秒伴奏。';
  } catch (error) {
    debug.error('导出 WAV', error);
    exportStatus.textContent = '无法导出伴奏，可以重试。';
  } finally {
    exporting = false;
    updateExport();
  }
});

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

async function enableVoice(live) {
  if (live.voice || !live.id) return;
  const voice = {listening: false};
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
        if (voice.captions && nod.enabled()) {
          voice.captions.reviewRevision += 1;
          nod.clear();
        }
        voiceStatus.textContent = '正在听 · 伴奏已压低。';
      } else if (data.samples && (captureMode.value === 'speech' ? Boolean(voice.captions) : musicCaptureAvailable)) {
        queueVoiceInput(live, voice, data);
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
    startCaptions(live, voice);
    controls();
  } catch (error) {
    if (run !== live || live.voice !== voice) return;
    releaseVoice(live);
    controls();
    voiceStatus.textContent = error.name === 'NotAllowedError' ? '请允许麦克风权限，然后点「开启麦克风」。' :
      '无法开启麦克风，可以重试或使用文字。';
  }
}

function queueVoiceInput(live, voice, data) {
  const captions = voice.captions;
  const segment = {...data, mode: captureMode.value, revision: voice.captureRevision,
    correctHumming: hummingCorrectionReady && correctHumming.checked, captions, reviewRevision: captions?.reviewRevision};
  if (!voice.captureJob) {
    recognizeVoiceInput(live, voice, segment);
  } else if (!voice.pendingSegment) {
    voice.pendingSegment = segment;
  } else {
    voiceStatus.textContent = '还有一段录音在等待识别，请稍等后再说。';
  }
}

async function recognizeVoiceInput(live, voice, segment) {
  const {mode, revision, captions} = segment;
  const current = () => run === live && live.voice === voice && revision === voice.captureRevision;
  const transcribe = Boolean(captions && voice.captions === captions);
  if (!current() || (mode === 'speech' && !transcribe)) return;
  voice.captureJob = true;
  voiceStatus.textContent = mode === 'speech' ? '正在转写…' : '正在识别这段输入…';
  if (transcribe) captionsStatus.textContent = '· 正在识别';
  try {
    let result;
    if (mode !== 'humming') {
      const audio = await encodeWave(segment.samples, segment.sampleRate);
      if (!current()) return;
      result = await post('capture', {session: live.id, mime_type: 'audio/wav', audio, mode, transcribe,
        humming_backend: 'basic-pitch', rhythm_timing: rhythmTiming.value}, transcribe ? 65000 : 28000);
      if (!current()) return;
    }
    if (mode === 'humming' || result?.recognition === 'analyze_humming') {
      voiceStatus.textContent = 'Basic Pitch 正在识别哼唱…';
      const buffer = live.clock.createBuffer(1, segment.samples.length, segment.sampleRate);
      buffer.copyToChannel(segment.samples, 0);
      const melody = await analyzeJamHumming(buffer);
      if (!current()) return;
      if (melody.notes.length && segment.correctHumming) voiceStatus.textContent = '正在矫正哼唱…';
      result = melody.notes.length ? await post('capture', {session: live.id, mode, melody,
        correct_humming: segment.correctHumming}, 28000) :
        {recognition: 'no_melody'};
    }
    if (!current()) return;
    if (result.event) {
      await sendToJam(live, null, result.event.source, voice, result.event);
      if (current()) {
        const correction = result.event.payload?.melody?.llmCorrection;
        voiceStatus.textContent = result.event.source === 'rhythm' ? '已识别拍手节奏。' :
          correction?.status === 'applied' ? '已识别哼唱，矫正 ' + correction.edits.length + ' 处。' :
          correction?.status === 'fallback' ? '哼唱矫正未完成，已采用基础清理。' : '已识别哼唱旋律。';
      }
    } else if (result.transcript) {
      await handleTranscript(live, voice, segment, result);
      if (current()) voiceStatus.textContent = '正在监听。';
    } else {
      voiceStatus.textContent = {no_rhythm: '未识别到拍手，请跟着伴奏拍一小节。',
        no_melody: '未识别到旋律，请再哼一句。', browser_speech: '语音转录尚未开启。',
        no_speech: '没听清这一句，正在监听。',
        unclassified: '未识别到拍手或旋律。'}[result.recognition] ?? '正在监听。';
    }
  } catch (error) {
    if (current()) {
      voiceStatus.textContent = error.name === 'AbortError' ? '识别超时，请重试。' : error.message;
      if (transcribe && voice.captions === captions) captionsStatus.textContent = '· 本段转写失败';
      debug.error('麦克风识别', error);
    }
  } finally {
    voice.captureJob = false;
    if (transcribe && current() && voice.captions === captions && captionsStatus.textContent === '· 正在识别') {
      captionsStatus.textContent = captions.final ? '· 最近一句' : '· 等待说话';
    }
    const pending = voice.pendingSegment;
    voice.pendingSegment = null;
    if (pending) recognizeVoiceInput(live, voice, pending);
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
openingMode.addEventListener('change', controls);
coldBpm.addEventListener('input', controls);
coldKey.addEventListener('change', controls);
timbreOverride.addEventListener('change', controls);
captureMode.addEventListener('change', () => {
  const voice = run?.voice;
  if (voice) {
    voice.captureRevision = (voice.captureRevision ?? 0) + 1;
    voice.pendingSegment = null;
    stopCaptions(voice);
    if (voice.processor) startCaptions(run, voice);
  } else {
    voiceStatus.textContent = voiceAvailability();
  }
  controls();
});
profile.addEventListener('change', () => {
  selectTimbres(timbreCatalog.presets[profile.value], profile.value);
  sendTimbres(run);
});
for (const select of Object.values(timbreSelects)) select.addEventListener('change', () => {
  profile.value = 'custom';
  sendTimbres(run);
});
autoPreview.addEventListener('change', () => {
  if (!run) return;
  if (autoPreview.checked) scheduleAutoPreview(run);
  else stopAutoPreview(run);
});
for (const event of ['play', 'pause', 'ended', 'error']) previewAudio.addEventListener(event, () => {
  if (!run) return;
  if (previewPlaying()) stopAutoPreview(run);
  setListening(run, Boolean(run.voice?.listening));
});
window.addEventListener('pagehide', () => { if (run) end(); else manual.stop(); });
post('status', {}).then(view => {
  agent.options[1].disabled = !view.llm_ready;
  arranger.options[1].disabled = !view.llm_ready;
  renderer.querySelector('option[value="fluidsynth"]').disabled = !view.fluidsynth;
  renderer.querySelector('option[value="mrt2"]').disabled = !view.mrt2;
  fillTimbres(view.timbre_catalog);
  speechStatus = ['server', 'gemini'].includes(view.speech_mode) && view.speech_ready ? 'ready' : 'unsupported';
  musicCaptureAvailable = Boolean(view.music_capture);
  hummingSeconds = view.humming_clip_seconds ?? 0;
  rhythmOpeningSeconds = view.rhythm_opening_seconds ?? 18;
  openingReady = Boolean(view.llm_ready || view.local_scoped_opening);
  hummingCorrectionReady = Boolean(view.humming_correction_ready);
  hummingCompletionReady = Boolean(view.opening_modes?.includes('humming'));
  coldAvailable = Boolean(view.opening_modes?.includes('cold'));
  if (!view.llm_ready && coldAvailable && !run) openingMode.value = 'cold';
  coldSettingsAvailable = view.cold_opening_settings === true;
  coldMrtReady = view.cold_mrt2_preview === true;
  mrtLiveState = view.mrt2_live_state === true;
  openingChecked = true;
  mrtReady = Boolean(view.mrt2);
  mrt2StreamReady = Boolean(view.mrt2_stream && window.ReadableStream);
  mrt2DirectReady = Boolean(view.mrt2_direct && window.ReadableStream);
  voiceStatus.textContent = voiceAvailability();
  controls();
  if (run?.voice?.processor) startCaptions(run, run.voice);
  else if (run?.id && voiceEnabled() && micSupported) enableVoice(run);
}).catch(() => {
  openingChecked = true;
  speechStatus = 'unreachable';
  voiceStatus.textContent = voiceAvailability();
  controls();
});
