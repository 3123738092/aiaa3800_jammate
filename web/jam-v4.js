import {decodeBase64, StudioPlayback, wave} from './jam-v4-playback.js';

const $ = id => document.getElementById(id);
const studio = window.JamMate;
const wavPlayer = $('backend-preview');
const exportButton = $('backend-export');
const config = {bpm: studio.getConfig().bpm, meter: '4/4'};
const requestedAsr = new URLSearchParams(location.search).get('asr');
const asrProvider = ['xfyun', 'qwen'].includes(requestedAsr) ? requestedAsr : undefined;
let tempoExplicit = false;
const volume = {band: studio.getConfig().volumes.band};
const requests = new Map();
const traces = new Map();
let live, preparing, timer, publish, service, previewUrl, previewShown, previewPending, savedTake;
let loadedRelease, releaseStatus;
let checkingRelease = false;
let backendPreference;
try { backendPreference = localStorage.getItem('jammate-llm-backend'); } catch {}
let failureCount = 0;
let pendingTempo, pendingMeter;
let lastActivity = performance.now();
let offer, offeredThisLull = false, requestingOffer = false;
let activityVersion = 0;
const suggestion = $('studio-suggestion');
const idleDelayMs = 8000;
const decisionBusy = new Set(['buffered', 'thinking', 'waiting_ready', 'arranging', 'scheduled']);

function hideOffer() {
  suggestion.hidden = true;
  offer = null;
}

function activity(rearm = true) {
  activityVersion++;
  lastActivity = performance.now();
  if (rearm) offeredThisLull = false;
  hideOffer();
}

async function checkIdle() {
  if (!studio.connected || offeredThisLull || requestingOffer) return;
  if (document.hidden || $('backend-dialog').open || studio.isPerforming()
      || $('txt').value.trim() || studio.getDrafts().length
      || preparing || (live && (live.clock.state !== 'running' || !live.firstBar || live.decisionBusy))) {
    lastActivity = performance.now();
    return;
  }
  if (performance.now() - lastActivity < idleDelayMs) return;
  const session = live?.id;
  const version = activityVersion;
  requestingOffer = true;
  try {
    const result = await post('studio-suggestion', session ? {session} : {});
    if (version !== activityVersion || session !== live?.id || document.hidden || preparing
        || studio.isPerforming() || $('txt').value.trim() || studio.getDrafts().length) return;
    offeredThisLull = true;
    if (!result.offer) return;
    offer = result.offer;
    $('suggestion-text').textContent = offer.message;
    suggestion.hidden = false;
  } catch {
    lastActivity = performance.now();
  } finally {
    requestingOffer = false;
  }
}

async function post(operation, body, timeout = 12_000) {
  const response = await fetch('/api/jam/' + operation, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body), signal: AbortSignal.timeout(timeout),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'JamMate 服务请求失败。');
  return result;
}

function send(event) { publish?.(event); }
function state(data, requestId) { send({type: 'state', requestId, state: data}); }

function trace(id, stage, status, data, requestId) {
  if (data == null) return;
  const key = id + '/' + stage;
  const content = JSON.stringify([status, data]);
  if (traces.get(key) === content) return;
  traces.set(key, content);
  if (traces.size > 350) traces.delete(traces.keys().next().value);
  send({type: 'trace', traceId: id, requestId: requestId ?? requests.get(id), stage, status,
    ...(stage === 'capture' || stage === 'input_event' ? {input: data} : {output: data})});
}

function reply(id, message, status) {
  if (message) send({type: 'agent.reply', messageId: id, requestId: requests.get(id), message, status});
}

function idle(phase = 'ready') {
  state({...config, phase, key: null, chord: null, progression: null, progressionIndex: null,
    bar: null, tracks: null, scheduledNotes: null, playbackReceipt: null, volumes: {...volume}});
}

function saveRequest(id, requestId) {
  requests.set(id, requestId);
  if (requests.size > 72) requests.delete(requests.keys().next().value);
}

function studioProfile() {
  const provider = service?.llm_backends?.find(item => item.id === backendPreference)
    ?? service?.llm_backends?.find(item => item.id === service.llm_backend_default);
  return {agent: service?.llm_ready ? 'llm' : 'rule',
    arranger: service?.llm_ready ? 'llm_all' : 'rule',
    llm_backend: provider?.id,
    renderer: service?.mrt2_stream ? 'mrt2_stream' : service?.studio_requires_stream ? null : 'synth'};
}

function renderConnection() {
  const ready = service?.studio_v4 === true;
  const profile = studioProfile();
  $('backend-agent').textContent = !ready ? '未连接' : profile.agent === 'llm' ? '大模型 Agent' : '本地规则';
  $('backend-arranger').textContent = !ready ? '未连接' : profile.arranger === 'llm_all' ? '大模型编排' : '本地规则';
  $('backend-renderer').textContent = !ready ? '未连接' : profile.renderer === 'mrt2_stream'
    ? 'MRT2 流式伴奏' : profile.renderer === null ? 'MRT2 流式不可用' : '本地合成器';
  $('backend-stop').disabled = !live?.id;
  const choices = service?.llm_backends ?? [];
  const selector = $('llm-backend');
  $('model-setting').hidden = choices.length === 0;
  if ([...selector.options].map(option => option.value).join() !== choices.map(item => item.id).join()) {
    selector.replaceChildren(...choices.map(item => new Option(item.label, item.id)));
  }
  selector.value = profile.llm_backend ?? '';
  selector.disabled = Boolean(live || preparing);
  $('model-setting-note').textContent = selector.disabled
    ? '结束会话后可切换' : '下次开演时生效';
  const model = choices.find(item => item.id === profile.llm_backend)?.model ?? service?.llm_model;
  const features = [
    service?.llm_ready ? (model || '大模型') : '大模型未配置',
    service?.speech_ready ? '语音可用' : '语音未配置',
    service?.release ? '后端 ' + service.release.slice(0, 7) : null,
  ];
  $('backend-note').textContent = !ready ? '尚未连上支持 Studio v4 的 JamMate 服务。'
    : features.filter(Boolean).join(' · ');
  showRelease();
}

function showRelease() {
  const notice = $('release-notice');
  const message = $('release-message');
  const action = $('release-action');
  const status = releaseStatus;
  const updated = loadedRelease && status?.release && loadedRelease !== status.release;
  const pending = status?.available_release && status.available_release !== status.release;
  const busy = live || preparing || studio.isPerforming();
  let text = '', label = '';
  if (updated) {
    text = status.local ? 'Mac 后端已更新' : 'JamMate 已更新';
    label = status.local ? '重新连接' : '刷新页面';
    if (busy) {
      text += '。可以先完成手上的操作。';
      label = '';
    }
  } else if (status?.updating) {
    text = 'JamMate 正在切换版本，请稍等。';
  } else if (pending) {
    text = '新版本已就绪，等当前会话结束后自动发布。';
    if (live && !preparing && !studio.isPerforming()) label = '结束会话';
  } else if (status?.deployment?.release && status.deployment.release !== status.release) {
    const version = status.deployment.release.slice(0, 7);
    text = status.deployment.phase === 'delayed'
      ? '更新 ' + version + ' 准备遇到问题，将自动重试。'
      : '发现新版本 ' + version + '，正在准备更新。';
  }
  if (message.textContent !== text) message.textContent = text;
  if (action.textContent !== label) action.textContent = label;
  action.hidden = !label;
  notice.hidden = !text;
}

async function checkRelease() {
  if (checkingRelease || document.hidden) return;
  checkingRelease = true;
  try {
    const response = await fetch('/api/ready', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
    if (!response.ok) return;
    const status = await response.json();
    loadedRelease ||= service?.release || status.release;
    releaseStatus = {...status, local: response.headers.get('X-JamMate-Frontend') === 'local'};
  } catch {
    if (releaseStatus?.available_release && !live) releaseStatus.updating = true;
  } finally {
    checkingRelease = false;
    showRelease();
  }
}

$('release-action').onclick = () => {
  if (live) {
    stop();
    void checkRelease();
  } else if (!preparing && !studio.isPerforming()) {
    const hasDraft = $('txt').value.trim() || studio.getDrafts().length;
    if (!hasDraft || window.confirm('刷新会清除未发送的素材和文字。继续刷新？')) window.location.reload();
  }
};

async function connect() {
  if (studio.connected) return;
  try {
    service = await post('status', {});
    if (!service.studio_v4) throw new Error('Studio v4 API 未启用。');
    studio.connect({
      label: 'JamMate',
      capabilities: {pause: true, tempo: true, meter: true, batchInput: true, independentMix: false,
        voiceWake: service.voice_wake === true},
      dispatch,
      subscribe(receive) {
        publish = receive;
        idle();
        return () => { publish = null; stop(); };
      },
    });
  } catch {
    service = null;
  }
  renderConnection();
}

function resetPreview() {
  previewShown = previewPending = null;
  wavPlayer.pause();
  wavPlayer.removeAttribute('src');
  wavPlayer.load();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  for (const id of ['backend-preview', 'backend-preview-status', 'backend-preview-download']) $(id).hidden = true;
}

function outputVisibility() {
  $('backend-output').hidden = exportButton.hidden && $('backend-preview-status').hidden && wavPlayer.hidden;
}

async function preview(active, view) {
  if (view.renderer !== 'mrt2') return;
  const id = view.auto_preview_id;
  active.eligiblePreview = id;
  const result = view.feedback ? view.preview_status : view.opening_preview;
  const label = $('backend-preview-status');
  if (previewShown && previewShown !== id) {
    wavPlayer.pause();
    wavPlayer.removeAttribute('src');
    wavPlayer.load();
    URL.revokeObjectURL(previewUrl);
    previewUrl = previewShown = null;
    wavPlayer.hidden = $('backend-preview-download').hidden = true;
  }
  label.hidden = !result;
  label.textContent = {generating: '正在生成 MRT2 试听', waiting_material: '等待生成素材',
    completed: 'MRT2 试听 · 独立音频', failed: result?.error || 'MRT2 试听生成失败'}[result?.status] || '';
  if (id && previewShown !== id && !previewPending) {
    previewPending = id;
    try {
      const data = await post('preview', {session: active.id, id}, 30_000);
      if (live !== active || active.eligiblePreview !== id) return;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(new Blob([decodeBase64(data.wav)], {type: 'audio/wav'}));
      wavPlayer.src = previewUrl;
      $('backend-preview-download').href = previewUrl;
      wavPlayer.hidden = $('backend-preview-download').hidden = false;
      previewShown = data.id;
    } catch (error) {
      if (live === active) label.textContent = error.message;
    } finally {
      if (live === active) previewPending = null;
    }
  }
  if (live === active) outputVisibility();
}

async function inspect(active) {
  const data = await post('debug', {session: active.id});
  if (live !== active) return;
  if (data.opening) {
    const id = active.id;
    trace(id, 'capture', 'accepted', data.opening.suggestion);
    trace(id, 'input_event', 'received', data.opening.input_events);
    if (data.opening.model) trace(id, 'model_response', 'completed', {
      model: data.opening.model, reason: data.opening.reason, response: data.opening.reply,
    });
    trace(id, 'band_plan', 'scheduled', data.opening.melody_replay_plan ?? data.opening.mrt2_plan ?? data.opening.solo_plan ?? data.opening.rhythm_plan);
    trace(id, 'render', data.opening.mrt2_preview?.status, data.opening.mrt2_preview);
    trace(id, 'playback', 'applied', data.opening.playback_receipt);
  }
  for (const turn of [...data.turns].reverse()) {
    const id = turn.input_event.event_id;
    trace(id, 'capture', 'accepted', turn.suggestion);
    trace(id, 'input_event', 'received', turn.input_events ?? turn.input_event);
    trace(id, 'jam_state', 'received', turn.decision_state);
    trace(id, 'model_response', turn.error ? 'error' : 'received', turn.decision);
    trace(id, 'band_plan', turn.plan_status?.status ?? turn.feedback?.status, turn.bound_plan ?? turn.proposed_plan);
    trace(id, 'arrangement', turn.arrangement?.status, turn.arrangement);
    trace(id, 'render', turn.inference?.status ?? 'prepared', turn.inference ?? turn.renderer_input);
    trace(id, 'playback', 'applied', turn.playback_receipt);
    trace(id, 'error', 'error', turn.error);
  }
  const stream = data.mrt2_stream;
  const bar = stream?.recent_bars?.at(-1);
  if (bar) trace(active.id, 'render', 'completed', {
    renderer: data.renderer, ...bar, generated_frames: stream.generated_frames,
    buffer_reconnections: active.gaps, bar_boundary_reconnections: active.boundaryGaps,
  });
  const scheduledNotes = (data.queued?.notes ?? data.current?.notes ?? []).map(note => ({
    pitch: note.pitch, startBeat: note.start, durationBeats: note.duration, track: note.instrument,
  }));
  state({scheduledNotes, jamState: data.jam_state, playbackGaps: active.gaps});
}

function showView(active, view) {
  const feedback = view.feedback;
  active.decisionBusy = decisionBusy.has(feedback?.status);
  if (!active.firstBar && view.current_bar != null) {
    active.firstBar = true;
    activity(false);
  }
  if (feedback && active.lastFeedback !== JSON.stringify(feedback)) {
    if (feedback.message) activity(false);
    active.lastFeedback = JSON.stringify(feedback);
    const status = {buffered: 'running', thinking: 'running', arranging: 'running',
      scheduled: 'scheduled', applied: 'completed', held: 'needs_input', expired: 'error',
      superseded: 'error'}[feedback.status];
    reply(feedback.id, feedback.message, status);
  }
  if (pendingTempo) {
    const {id, requestId} = pendingTempo;
    if (feedback?.id === id && feedback.status === 'applied') {
      config.bpm = view.bpm;
      pendingTempo = null;
      state({bpm: view.bpm}, requestId);
    } else if (feedback?.id === id && ['held', 'expired', 'superseded'].includes(feedback.status)) {
      pendingTempo = null;
      send({type: 'error', requestId, message: '速度调整未应用。'});
    }
  }
  if (pendingMeter && feedback?.id === pendingMeter.id
      && ['applied', 'held', 'expired', 'superseded'].includes(feedback.status)) {
    if (feedback.status !== 'applied') send({type: 'error', requestId: pendingMeter.requestId,
      message: '拍号调整未应用。'});
    pendingMeter = null;
  }
  config.bpm = view.bpm;
  config.meter = view.meter ?? config.meter;
  const next = {
    phase: active.clock.state === 'suspended' ? 'paused' : !view.playing ? 'ended' :
      view.current_bar == null ? 'starting' : 'playing',
    bpm: view.bpm, meter: view.meter ?? config.meter, key: view.key, chord: view.chord,
    progression: view.progression, progressionIndex: view.progression_index, bar: view.current_bar,
    playbackReceipt: view.last_applied,
    tracks: view.layers.map(layer => ({id: layer.name, name: layer.label ?? layer.name,
      enabled: layer.active, density: layer.density})),
  };
  const signature = JSON.stringify(next);
  if (signature !== active.lastState) {
    active.lastState = signature;
    state(next);
  }
  if (view.current_bar != null) exportButton.hidden = false;
  outputVisibility();
  void preview(active, view);
}

async function poll(active) {
  try {
    const view = await post('poll', active.position());
    if (live !== active) return;
    active.confirmedBar = view.current_bar ?? -1;
    await active.enqueue(view.next);
    if (live !== active) return;
    failureCount = 0;
    showView(active, view);
    if (active.debugAt <= performance.now()) {
      active.debugAt = performance.now() + 900;
      void inspect(active).catch(() => {});
    }
    if (!view.playing) {
      stop('ended');
      return;
    }
  } catch (error) {
    if (live !== active) return;
    if (++failureCount >= 3) {
      failPlayback(error);
      return;
    }
  }
  if (live === active) timer = setTimeout(() => poll(active), 100);
}

function release(id) {
  if (id) fetch('/api/jam/stop', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session: id}), keepalive: true}).catch(() => {});
}

function stop(phase = 'ended', retain = true) {
  clearTimeout(timer);
  activity();
  const active = live;
  live = null;
  preparing = null;
  pendingTempo = pendingMeter = null;
  if (active) {
    if (retain && !exportButton.hidden) savedTake = active.export().catch(() => null);
    active.close();
    release(active.id);
  }
  if (retain) idle(phase);
  renderConnection();
}

function failPlayback(error) {
  stop('error');
  send({type: 'error', message: error.message || '播放中断。'});
}

async function launch(active, opening, requestId, {ideaId, openingMode} = {}) {
  if (live) throw new Error('已有演奏正在进行。');
  live = active;
  activity(false);
  failureCount = 0;
  savedTake = null;
  exportButton.hidden = true;
  resetPreview();
  outputVisibility();
  renderConnection();
  state({phase: 'starting', key: null, chord: null, progression: null, progressionIndex: null,
    scheduledNotes: null, jamState: null, playbackReceipt: null, tracks: null});
  try {
    const profile = studioProfile();
    if (profile.renderer === null) throw new Error('MRT2 流式伴奏暂不可用，请稍后重新开演。');
    openingMode ??= profile.agent === 'llm' && opening.text ? 'warm' : 'cold';
    const useConfiguredTempo = tempoExplicit || opening.midi || opening.humming || (opening.rhythm && !opening.rhythm.hits);
    const bpm = useConfiguredTempo && !opening.rhythm?.count_in_ms?.length ? config.bpm : null;
    const view = await post('start', {opening, studio_idea: ideaId, opening_mode: openingMode, bpm,
      meter: config.meter, ...profile, stream_lookahead: profile.renderer === 'mrt2_stream'}, 100_000);
    if (live !== active) {
      release(view.session);
      throw new Error('会话已取消。');
    }
    active.attach(view, {lossless: service?.mrt2_flac});
    config.meter = view.meter ?? config.meter;
    active.debugAt = 0;
    saveRequest(view.session, requestId);
    await active.enqueue(view.next);
    if (live !== active) throw new Error('会话已取消。');
    renderConnection();
    void poll(active);
  } catch (error) {
    if (live === active) stop('ready');
    throw error;
  }
}

async function openPlayback() {
  const clock = new AudioContext();
  let timeout;
  try {
    await Promise.race([clock.resume(), new Promise((_, reject) => {
      timeout = setTimeout(() => reject(new DOMException('浏览器限制了自动播放，请点一次播放或发送。',
        'PlaybackGestureRequired')), 1800);
    })]);
    return new StudioPlayback(clock, volume.band, failPlayback);
  } catch (error) {
    void clock.close();
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function audioWave(blob, seconds) {
  const context = new OfflineAudioContext(1, 1, 16000);
  const decoded = await context.decodeAudioData(await blob.arrayBuffer());
  if (!(decoded.duration > 0) || decoded.duration > seconds + .15) {
    throw new Error('请提交 ' + seconds + ' 秒以内的录音。');
  }
  const frames = Math.min(seconds * 16000, decoded.length);
  const pcm = new Int16Array(frames);
  const channels = Array.from({length: decoded.numberOfChannels}, (_, c) => decoded.getChannelData(c));
  for (let i = 0; i < frames; i++) {
    let sample = 0;
    for (const channel of channels) sample += channel[i] / channels.length;
    pcm[i] = Math.round(Math.max(-1, Math.min(1, sample)) * 32767);
  }
  const bytes = new Uint8Array(await wave(pcm, 16000).arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
  return {mime_type: 'audio/wav', audio: btoa(binary)};
}

async function prepareInput(action) {
  const input = {target: action.contextTarget ?? action.target ?? 'band',
    text: action.text ?? action.instruction ?? '', rhythm_timing: 'feel'};
  if (action.type === 'input.text' && action.source === 'speech') input.source = 'speech';
  const materials = action.type === 'input.bundle' ? action.inputs : action.type === 'input.text' ? [] : [action];
  for (const item of materials) {
    if (item.type === 'input.pads') {
      if (input.rhythm) throw new Error('拍手和鼓垫节奏请分开提交。');
      input.rhythm = {hits: item.hits, bpm: item.bpm, meter: item.meter, count_in_ms: item.countInMs};
      continue;
    }
    if (item.type === 'input.midi') {
      if (input.midi || input.humming) throw new Error('请逐段提交键盘旋律和哼唱。');
      input.midi = {events: item.events, source_bpm: item.sourceBpm, origin: item.origin, target: item.target};
      continue;
    }
    let mode = item.mode;
    const seconds = {auto: 20, humming: 20, rhythm: 18}[mode] ?? 30;
    const recording = await audioWave(item.audio, seconds);
    recording.target = item.target;
    if (mode === 'auto') {
      const classified = await post('studio-classify', {...recording, bpm: config.bpm, meter: config.meter}, 24_000);
      mode = classified.mode;
      if (!mode) throw new Error('没有识别到可用的音乐或语音。请重录或指定输入模式。');
    }
    if (mode === 'speech') {
      const recognized = await post('opening-mic', {...recording, mode: 'dictation', asr_provider: asrProvider}, 48_000);
      if (!recognized.instruction) throw new Error('没有转写出文字，请重录或改用文字输入。');
      send({type: 'transcript', requestId: action.requestId, text: recognized.instruction});
      trace(action.requestId, 'recognition', 'completed', recognized, action.requestId);
      input.text = [recognized.instruction, input.text].filter(Boolean).join('\n');
      input.source = 'speech';
      if (!action.instruction) input.target = item.target;
    } else {
      const key = mode === 'rhythm' ? 'rhythm' : 'humming';
      if (input[key] || (key === 'humming' && input.midi)) throw new Error('同一类素材请逐段提交。');
      input[key] = recording;
    }
    send({type: 'input.interpretation', mode, target: item.target});
  }
  return input;
}

async function voiceTurn(action) {
  const previous = live;
  const pcm = new Int16Array(action.samples.length);
  for (let i = 0; i < pcm.length; i++) {
    pcm[i] = Math.round(Math.max(-1, Math.min(1, action.samples[i])) * 32767);
  }
  const recording = await audioWave(wave(pcm, action.sampleRate), 13);
  const result = await post('opening-mic', {...recording, mode: 'wake', wake_target: action.wakeTarget,
    asr_provider: asrProvider}, 52_000);
  if (!action.current() || live !== previous) return {};
  if (result.target) send({type: 'voice.wake', target: result.target});
  if (!result.instruction) return result;
  send({type: 'transcript', requestId: action.requestId, text: result.instruction});
  trace(action.requestId, 'recognition', 'completed', result, action.requestId);
  try {
    await dispatch({...action, type: 'input.text', target: result.target, text: result.instruction, source: 'speech'});
  } catch (error) {
    if (error.name !== 'PlaybackGestureRequired') throw error;
    const composer = $('txt');
    if (!composer.value.trim()) {
      composer.value = result.instruction;
      composer.dispatchEvent(new Event('input', {bubbles: true}));
    }
    reply(action.requestId, '听到了：' + result.instruction + '。浏览器限制了自动播放，可以点发送继续。', 'needs_input');
    return {};
  }
  return {sent: true};
}

async function dispatch(action) {
  if (action.type === 'voice.turn') return voiceTurn(action);
  const requestId = action.requestId;
  activity();
  switch (action.type) {
    case 'transport.start': {
      if (live || preparing) throw new Error('正在准备另一条输入。');
      preparing = requestId;
      try {
        const active = await openPlayback();
        await launch(active, {text: 'Full band accompaniment, ' + config.bpm + ' BPM', target: 'band'}, requestId,
          {openingMode: 'cold'});
      } finally {
        preparing = null;
        renderConnection();
      }
      return;
    }
    case 'transport.pause':
    case 'transport.resume': {
      if (!live?.id) throw new Error('当前没有可以暂停或继续的会话。');
      const pause = action.type === 'transport.pause';
      if (!pause) wavPlayer.pause();
      await (pause ? live.clock.suspend() : live.clock.resume());
      state({phase: pause ? 'paused' : 'playing'}, requestId);
      return;
    }
    case 'meter.set': {
      if (!['3/4', '4/4', '6/8'].includes(action.meter)) throw new Error('请选择 4/4、3/4 或 6/8。');
      if (preparing || (live && !live.id)) throw new Error('乐队还在准备，请稍等。');
      if (!live) {
        config.meter = action.meter;
        state({meter: config.meter}, requestId);
        return;
      }
      if (live.clock.state !== 'running') throw new Error('继续演奏后可在小节边界换拍号。');
      const {feedback} = await post('feedback', {session: live.id, text: '切到 ' + action.meter + ' 拍'});
      saveRequest(feedback.id, requestId);
      pendingMeter = {id: feedback.id, requestId};
      return;
    }
    case 'tempo.set': {
      if (preparing || (live && !live.id)) throw new Error('请等待当前输入准备完毕。');
      if (!live) {
        config.bpm = action.bpm;
        tempoExplicit = true;
        state({bpm: config.bpm}, requestId);
        return;
      }
      if (live.clock.state !== 'running') throw new Error('继续演奏后可调整速度。');
      const {feedback} = await post('feedback', {session: live.id, text: '把速度调到 ' + action.bpm + ' BPM'});
      saveRequest(feedback.id, requestId);
      pendingTempo = {id: feedback.id, requestId};
      return;
    }
    case 'mix.volume':
      if (action.target !== 'band') throw new Error('当前输出是混合音频。');
      volume.band = action.value;
      live?.setVolume(action.value);
      wavPlayer.volume = action.value;
      return;
    case 'input.text':
    case 'input.midi':
    case 'input.pads':
    case 'input.audio':
    case 'input.bundle':
      break;
    default:
      throw new Error('当前服务没有提供此操作。');
  }
  if (preparing || (live && !live.id)) throw new Error('请等待上一条输入。');
  if (live?.clock.state === 'suspended') throw new Error('继续演奏后可提交输入，草稿已保留。');
  preparing = requestId;
  const previous = live;
  let active;
  try {
    if (!previous) active = await openPlayback();
    const input = await prepareInput(action);
    if (preparing !== requestId || live !== previous) throw new Error('会话已更改，请重新提交。');
    if (previous) {
      const {feedback} = await post('studio-input', {session: previous.id, input}, 35_000);
      saveRequest(feedback.id, requestId);
    } else await launch(active, input, requestId);
  } finally {
    if (active && live !== active && !active.abort.signal.aborted) active.close();
    if (preparing === requestId) preparing = null;
    renderConnection();
  }
}

$('suggestion-accept').onclick = async () => {
  const accepted = offer;
  if (!accepted || preparing) return;
  const requestId = crypto.randomUUID();
  let opening;
  activity(false);
  preparing = requestId;
  try {
    if (live?.id) {
      const {feedback} = await post('studio-suggestion', {session: live.id, accept: accepted.id});
      saveRequest(feedback.id, requestId);
    } else {
      opening = await openPlayback();
      await launch(opening, {text: accepted.message, target: 'band'}, requestId, {ideaId: accepted.id});
    }
  } catch (error) {
    send({type: 'error', requestId, message: error.message});
  } finally {
    if (opening && live !== opening && !opening.abort.signal.aborted) opening.close();
    if (preparing === requestId) preparing = null;
    renderConnection();
  }
};
$('suggestion-skip').onclick = () => activity(false);
$('txt').addEventListener('input', () => activity());
window.addEventListener('jammate:studio-activity', () => activity());
document.addEventListener('visibilitychange', () => { activity(false); void checkRelease(); });
window.addEventListener('focus', checkRelease);
window.addEventListener('pageshow', checkRelease);
setInterval(checkRelease, 5000);
const idleTimer = setInterval(checkIdle, 500);
$('backend-settings').onclick = () => {
  activity(false);
  $('backend-dialog').showModal();
  if (!studio.connected) void connect();
};
$('backend-stop').onclick = () => { stop(); $('backend-dialog').close(); };
$('llm-backend').onchange = event => {
  if (live || preparing) return;
  backendPreference = event.target.value;
  try { localStorage.setItem('jammate-llm-backend', backendPreference); } catch {}
  renderConnection();
};
wavPlayer.volume = volume.band;
wavPlayer.addEventListener('play', () => {
  const active = live;
  if (active?.clock.state !== 'running') return;
  void active.clock.suspend().then(() => {
    if (live === active) state({phase: 'paused'});
  });
});
exportButton.onclick = async () => {
  exportButton.disabled = true;
  try {
    const blob = live ? await live.export() : await savedTake;
    if (!blob) throw new Error('尚无可导出的演奏。');
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'JamMate-performance.wav';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    send({type: 'error', message: error.message});
  } finally {
    exportButton.disabled = false;
  }
};
window.addEventListener('pagehide', () => { clearInterval(idleTimer); stop('ended', false); });
void connect().then(checkRelease);
