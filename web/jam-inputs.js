import {initOpeningMic} from './jam-opening-mic.js';
import {analyzeJamHumming} from './jam-humming.js';
import {decodeHummingAudio} from './optimized/src/audio-pipeline.js';

const rate = 16000;
const titles = {rhythm: '拍手', humming: '哼唱'};

export function initJamInputs({post, encodeWave, waveBlob, liveSession, onChange, onRecord, sendEvent, correctHumming}) {
  const root = document.getElementById('jam-opening');
  const text = document.getElementById('jam-opening-text');
  const status = document.getElementById('jam-opening-status');
  const result = document.getElementById('jam-opening-result');
  const cards = [...root.querySelectorAll('[data-jam-input]')].map(node => ({
    node, mode: node.dataset.jamInput, record: node.querySelector('[data-record]'), file: node.querySelector('input[type=file]'),
    guide: node.querySelector('[data-guide]'), status: node.querySelector('[data-input-status]'),
    audio: node.querySelector('[data-input-audio]'), send: node.querySelector('[data-send]'), remove: node.querySelector('[data-clear]'),
    trim: node.querySelector('[data-humming-trim]'), trimStart: node.querySelector('[data-trim-start]'),
    trimRange: node.querySelector('[data-trim-range]'), trimAudio: node.querySelector('[data-trim-audio]'),
    openingGuide: node.querySelector('[data-guide]').textContent, clip: null, selection: null, busy: false, dragDepth: 0,
  }));
  const supportsRecording = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
  let take = null;
  let pending = false;
  let hummingSeconds = 0;
  const mic = initOpeningMic({text, post, encodeWave, onChange,
    async receiveMusic(mode, blob) {
      const card = cards.find(item => item.mode === mode);
      card.node.classList.add('mic-target');
      card.node.scrollIntoView({block: 'nearest'});
      if (blob) {
        await decode(card, blob, '总麦克风录音');
      } else {
        card.status.textContent = '正在打开麦克风…';
        await toggleRecord(card);
        if (take?.card === card && take.recorder?.state === 'recording') card.record.focus();
      }
    },
  });

  function durationLimit(card, live) {
    return (card.mode === 'humming' && hummingSeconds) || (live ? 9 : 12);
  }

  function clearTrimAudio(card) {
    const preview = card.trimAudio;
    if (preview?.hasAttribute('src')) {
      preview.pause();
      URL.revokeObjectURL(preview.src);
      preview.removeAttribute('src');
      preview.load();
      preview.hidden = true;
    }
  }

  function clearSelection(card) {
    clearTrimAudio(card);
    card.selection = null;
    if (card.trim) card.trim.hidden = true;
  }

  function clear(card) {
    clearSelection(card);
    if (card.clip) URL.revokeObjectURL(card.clip.url);
    card.clip = null;
    card.audio.removeAttribute('src');
    card.audio.hidden = true;
    card.status.textContent = '等待' + titles[card.mode] + '录音';
  }

  function update({starting = false, ready = false, checked = false, cold = false, hummingSeconds: limit = 0,
                   hummingCompletion = false} = {}) {
    pending = starting;
    hummingSeconds = limit;
    const live = liveSession();
    mic.update({live: Boolean(live), blocked: starting || Boolean(take) ||
      cards.some(card => card.busy || card.selection), supported: supportsRecording});
    const locked = pending || mic.busy();
    document.getElementById('jam-opening-direction').hidden = Boolean(live);
    text.disabled = pending;
    for (const card of cards) {
      card.record.textContent = take?.card === card && take.recorder ? '停止录制' : '录' + titles[card.mode];
      card.record.disabled = !supportsRecording || locked || card.busy ||
        Boolean(take && (take.card !== card || !take.recorder));
      card.file.disabled = locked || card.busy || Boolean(take);
      if (card.file.disabled) resetDrop(card);
      card.remove.hidden = !card.clip || Boolean(card.selection);
      card.remove.disabled = locked || card.busy || Boolean(take);
      card.send.hidden = !live;
      card.send.disabled = !card.clip || card.busy || Boolean(take || card.selection);
      if (card.trim) card.trim.disabled = locked || card.busy || Boolean(take);
      card.guide.textContent = live && card.mode === 'rhythm' ?
        '跟着当前速度拍一小节，拍完留到小节结束。' : card.openingGuide;
    }
    status.textContent = pending ? (cold ? '正在读取已提交的开场素材…' : '正在读取素材，并让 LLM 选择开场编制…') :
      cards.some(card => card.selection) ? '请先使用或取消所选哼唱片段。' :
      live ? '可以录制或上传新的素材，再手动发送到当前 Jam。' :
      !checked ? '正在检查开场编制模型…' :
      ready ? '文字、拍手和哼唱任选一种。也可以混合输入。' :
      hummingCompletion ? '暖启动需要 LLM 服务。可以使用自动补全哼唱。' : '开场需要可用的 LLM 服务。';
  }

  function mono(buffer, start = 0, end = buffer.length) {
    const samples = new Float32Array(end - start);
    for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
      const data = buffer.getChannelData(channel);
      for (let i = start; i < end; i++) samples[i - start] += data[i] / buffer.numberOfChannels;
    }
    return samples;
  }

  async function saveClip(card, live, buffer, name, blob, start = 0, end = buffer.length) {
    const melody = card.mode === 'humming' ? {buffer, start, end, name} : null;
    const audio = melody ? null : await encodeWave(mono(buffer, start, end), buffer.sampleRate);
    if (liveSession() !== live) return;
    clear(card);
    const duration = (end - start) / buffer.sampleRate;
    card.clip = {audio, melody, url: URL.createObjectURL(blob), label: name + ' · ' + duration.toFixed(1) + ' 秒'};
    card.audio.src = card.clip.url;
    card.audio.hidden = false;
    card.status.textContent = card.clip.label;
  }

  function trimBounds(card) {
    const {buffer, frames} = card.selection;
    const start = Math.max(0, Math.min(buffer.length - frames, Math.round(card.trimStart.valueAsNumber * buffer.sampleRate)));
    return {start, end: start + frames};
  }

  function showTrimRange(card) {
    clearTrimAudio(card);
    const {start, end} = trimBounds(card);
    const sampleRate = card.selection.buffer.sampleRate;
    card.trimRange.textContent = (start / sampleRate).toFixed(1) + '–' + (end / sampleRate).toFixed(1) + ' 秒';
  }

  async function decode(card, blob, name) {
    const live = liveSession();
    clearSelection(card);
    card.busy = true;
    onChange();
    try {
      const limit = durationLimit(card, live);
      if (blob.size > 25 * 1024 * 1024) throw new Error('文件太大，请截取 ' + limit + ' 秒以内的片段。');
      const OfflineAudio = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      if (!OfflineAudio) throw new Error('浏览器无法解码音频。');
      let buffer;
      try {
        const data = await blob.arrayBuffer();
        buffer = card.mode === 'humming' ? await decodeHummingAudio(data) :
          await new OfflineAudio(1, 1, rate).decodeAudioData(data);
      } catch {
        throw new Error('无法读取此音频，请尝试 WAV 或浏览器支持的音频格式。');
      }
      if (!(buffer.duration > 0)) throw new Error('这段录音没有音频内容。');
      if (liveSession() !== live) return;
      if (buffer.duration > limit && card.trim) {
        const frames = Math.floor(limit * buffer.sampleRate);
        card.selection = {buffer, frames, name};
        card.trimStart.max = (buffer.length - frames) / buffer.sampleRate;
        card.trimStart.value = '0';
        showTrimRange(card);
        card.trim.hidden = false;
        card.status.textContent = name + ' · ' + buffer.duration.toFixed(1) + ' 秒，请选择 ' + limit + ' 秒的片段。';
        return;
      }
      if (buffer.duration > limit) {
        throw new Error('请使用 ' + limit + ' 秒以内的' + titles[card.mode] + '录音。');
      }
      await saveClip(card, live, buffer, name, blob);
    } catch (error) {
      card.status.textContent = error.message + (card.clip ? ' 已保留上一段可用素材。' : '');
    } finally {
      card.busy = false;
      onChange();
    }
  }

  async function toggleRecord(card) {
    if (take?.card === card && take.recorder) {
      take.recorder.stop();
      return;
    }
    if (pending || take || card.busy) return;
    const capture = {card};
    take = capture;
    onChange();
    onRecord(true);
    try {
      capture.stream = await navigator.mediaDevices.getUserMedia({
        audio: {channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: false},
      });
      if (take !== capture) {
        capture.stream.getTracks().forEach(track => track.stop());
        return;
      }
      const recorder = new MediaRecorder(capture.stream);
      const chunks = [];
      capture.recorder = recorder;
      const release = () => {
        clearTimeout(capture.timer);
        capture.stream.getTracks().forEach(track => track.stop());
        if (take === capture) take = null;
        onRecord(false);
      };
      recorder.ondataavailable = ({data}) => { if (data.size) chunks.push(data); };
      recorder.onerror = () => {
        release();
        card.status.textContent = '录制中断，请重试或选择音频文件。';
        onChange();
      };
      recorder.onstop = () => {
        if (take !== capture) return;
        release();
        decode(card, new Blob(chunks, {type: recorder.mimeType}), titles[card.mode] + '录音');
      };
      const limit = durationLimit(card, liveSession());
      recorder.start();
      capture.timer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, limit * 1000 - 300);
      card.status.textContent = '正在录' + titles[card.mode] + '，可以开始了。结束后点「停止录制」。';
      onChange();
    } catch {
      capture.stream?.getTracks().forEach(track => track.stop());
      if (take === capture) take = null;
      onRecord(false);
      card.status.textContent = '无法使用麦克风。请检查权限或选择音频文件。';
      onChange();
    }
  }

  async function payload(card) {
    const clip = card.clip;
    if (!clip.melody) return {mime_type: 'audio/wav', audio: clip.audio};
    const correction = correctHumming();
    const melody = clip.analysis ?? await analyzeJamHumming(clip.melody.buffer, clip.melody);
    if (!melody.notes.length) throw new Error('未识别到稳定的哼唱音符，请重新录制。');
    clip.analysis = melody;
    return {melody, correct_humming: correction};
  }
  function resetDrop(card) {
    card.dragDepth = 0;
    card.node.classList.remove('is-dragging');
  }

  text.addEventListener('input', onChange);
  for (const card of cards) {
    if (card.trim) {
      card.trimStart.addEventListener('input', () => showTrimRange(card));
      card.trim.querySelector('[data-trim-preview]').addEventListener('click', () => {
        const {start, end} = trimBounds(card);
        const {buffer} = card.selection;
        clearTrimAudio(card);
        card.trimAudio.src = URL.createObjectURL(waveBlob(mono(buffer, start, end), buffer.sampleRate));
        card.trimAudio.hidden = false;
        card.trimAudio.play().catch(() => {});
      });
      card.trim.querySelector('[data-trim-apply]').addEventListener('click', async () => {
        const live = liveSession();
        const {start, end} = trimBounds(card);
        const {buffer, name} = card.selection;
        const label = card.trimRange.textContent;
        card.busy = true;
        onChange();
        try {
          const samples = mono(buffer, start, end);
          await saveClip(card, live, buffer, name + '（' + label + '）',
            waveBlob(samples, buffer.sampleRate), start, end);
        } catch (error) {
          card.status.textContent = error.message;
        } finally {
          card.busy = false;
          onChange();
        }
      });
      card.trim.querySelector('[data-trim-cancel]').addEventListener('click', () => {
        clearSelection(card);
        card.status.textContent = card.clip?.label ?? '等待哼唱录音';
        onChange();
      });
    }
    card.remove.addEventListener('click', () => { clear(card); onChange(); });
    card.record.addEventListener('click', () => toggleRecord(card));
    card.file.addEventListener('change', () => {
      const file = card.file.files[0];
      card.file.value = '';
      if (file && !card.file.disabled) decode(card, file, file.name);
    });
    card.node.addEventListener('dragenter', event => {
      if (!event.dataTransfer?.types.includes('Files')) return;
      event.preventDefault();
      if (card.file.disabled) return;
      card.dragDepth += 1;
      card.node.classList.add('is-dragging');
    });
    card.node.addEventListener('dragover', event => {
      if (!event.dataTransfer?.types.includes('Files')) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = card.file.disabled ? 'none' : 'copy';
    });
    card.node.addEventListener('dragleave', () => {
      if (card.dragDepth > 0 && --card.dragDepth === 0) resetDrop(card);
    });
    card.node.addEventListener('drop', event => {
      resetDrop(card);
      const transfer = event.dataTransfer;
      if (!transfer || !transfer.types.includes('Files')) return;
      event.preventDefault();
      if (card.file.disabled) return;
      if (transfer.files.length !== 1) {
        card.status.textContent = '请一次拖入一个音频文件。';
        return;
      }
      const [file] = transfer.files;
      decode(card, file, file.name);
    });
    card.send.addEventListener('click', async () => {
      const live = liveSession();
      if (!live || !card.clip || card.busy) return;
      card.busy = true;
      card.status.textContent = '正在识别' + titles[card.mode] + '…';
      onChange();
      try {
        const input = await payload(card);
        if (liveSession() !== live) return;
        if (input.correct_humming) card.status.textContent = '正在矫正哼唱…';
        const {event} = await post('capture', {session: live.id, mode: card.mode, ...input}, 28000);
        if (liveSession() !== live) return;
        if (!event) throw new Error(card.mode === 'rhythm' ?
          '未识别到节奏，请跟拍一整小节。' : '未识别到音符，请重新录制。');
        if (await sendEvent(live, event)) {
          const correction = event.payload?.melody?.llmCorrection;
          card.status.textContent = '已发送' + titles[card.mode] + '到当前 Jam。' +
            (correction?.status === 'applied' ? '矫正 ' + correction.edits.length + ' 处。' :
              correction?.status === 'fallback' ? '已采用基础清理。' : '');
        }
        else card.status.textContent = '发送失败，可以重试。';
      } catch (error) {
        card.status.textContent = error.name === 'AbortError' ? '识别超时，请重试。' : error.message;
      } finally {
        card.busy = false;
        onChange();
      }
    });
  }

  return {
    update,
    hasHumming: () => cards.some(card => card.mode === 'humming' && card.clip),
    ready: (hasSettings = false) => !mic.busy() && !take && cards.every(card => !card.busy && !card.selection) &&
      (hasSettings || Boolean(text.value.trim()) || cards.some(card => card.clip)),
    async request() {
      const entries = cards.filter(card => card.clip);
      const instruction = text.value.trim();
      const inputs = await Promise.all(entries.map(async card => [card.mode, await payload(card)]));
      return {...Object.fromEntries(inputs), text: instruction};
    },
    opened(opening, bpm) {
      for (const card of cards) clear(card);
      const voices = opening.instruments.map(item => item.label).join('、') || '等待素材';
      const key = opening.key?.replace(' major', ' 大调').replace(' minor', ' 小调') ?? '调性未指定';
      const modeNames = {cold: '冷启动', warm: '暖启动', humming: '自动补全哼唱'};
      document.getElementById('jam-opening-ensemble').textContent =
        modeNames[opening.mode] + ' · ' +
        Math.round(bpm * 10) / 10 + ' BPM · ' + key + ' · ' + voices;
      const sourceNames = {manual: '手动 BPM', text: '文字', claps: '拍手测量', model: '模型选择',
        clock: '默认播放时钟', humming_estimate: '哼唱估计'};
      const settings = opening.settings;
      const beats = settings.rhythm_beats_zero_based.map(beat => beat + 1).join('、');
      const soloHumming = opening.mode === 'cold' && opening.instruments.length === 1 &&
        opening.instruments[0].name === 'lead';
      document.getElementById('jam-opening-reason').textContent =
        ['速度来源 ' + sourceNames[settings.tempo_source], beats ? '每小节起音 ' + beats + ' 拍' : '',
         opening.progression?.length ? '和弦 ' + opening.progression.join(' → ') : '和弦未指定',
         soloHumming ? '这句播完后会等待新素材。用哼唱发展连续伴奏时，请选择暖启动。' : opening.reason]
          .filter(Boolean).join(' · ');
      result.hidden = false;
    },
    stop(clearClips = false) {
      mic.reset();
      result.hidden = true;
      if (clearClips) cards.forEach(clear);
      if (take) {
        const capture = take;
        take = null;
        clearTimeout(capture.timer);
        if (capture.recorder?.state === 'recording') capture.recorder.stop();
        capture.stream?.getTracks().forEach(track => track.stop());
        onRecord(false);
      }
    },
  };
}
