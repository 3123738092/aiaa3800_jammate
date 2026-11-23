// Opening takes are independent of the microphone used during a live Jam.
export function initOpeningMic({text, post, encodeWave, receiveMusic, onChange}) {
  const button = document.getElementById('opening-mic');
  const row = document.getElementById('opening-mic-row');
  const status = document.getElementById('opening-mic-status');
  const review = document.getElementById('opening-mic-review');
  const confirm = document.getElementById('opening-mic-confirm');
  const discard = document.getElementById('opening-mic-discard');
  let active = null;
  let original = null;
  let phase = 'idle';

  function show(next, message) {
    phase = next;
    status.textContent = message;
    onChange();
  }

  function clearHighlight() {
    document.querySelectorAll('#jam-opening .mic-target').forEach(node => node.classList.remove('mic-target'));
  }

  function release(op) {
    clearTimeout(op.timer);
    if (op.captions) {
      op.captions.onresult = null;
      try { op.captions.abort(); } catch { /* already stopped */ }
      op.captions = null;
    }
    op.stream?.getTracks().forEach(track => track.stop());
  }

  function draft(op, words) {
    text.value = [op.before.trimEnd(), words.trim()].filter(Boolean).join('\n');
  }

  function reviewWords(op, words) {
    original = op.before;
    draft(op, words);
    active = null;
    text.classList.add('mic-target');
    show('review', '请核对开场文字，可以修改后再确认。');
    text.focus();
  }

  function startCaptions(op) {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) return;
    try {
      const captions = op.captions = new Recognition();
      captions.lang = 'zh-CN';
      captions.continuous = true;
      captions.interimResults = true;
      captions.onresult = event => {
        if (active !== op || phase !== 'recording') return;
        op.preview = Array.from(event.results, result => result[0].transcript).join('').trim();
        draft(op, op.preview);
      };
      // The recorded take also supports browsers without live speech recognition.
      captions.onerror = () => {};
      captions.start();
    } catch { /* final transcription still uses the recorded audio */ }
  }

  async function audioPayload(blob) {
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const clock = new Offline(1, 1, 16000);
    const buffer = await clock.decodeAudioData(await blob.arrayBuffer());
    const samples = new Float32Array(buffer.length);
    for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
      const data = buffer.getChannelData(channel);
      for (let i = 0; i < data.length; i++) samples[i] += data[i] / buffer.numberOfChannels;
    }
    return encodeWave(samples, buffer.sampleRate);
  }

  function fail(op, error) {
    if (active !== op) return;
    release(op);
    if (op.mode === 'dictation' && op.preview) {
      reviewWords(op, op.preview);
      status.textContent = '最终听写未完成，已保留浏览器字幕，请校对后确认。';
      return;
    }
    if (op.mode === 'dictation') text.value = op.before;
    active = null;
    clearHighlight();
    const message = error.name === 'NotAllowedError' ? '浏览器未允许使用麦克风。请允许后重试。' :
      error.name === 'NotFoundError' ? '没有找到麦克风，请检查设备后重试。' :
      error.name === 'AbortError' ? '录音识别超时，请重试。' : error.message;
    show('idle', message || '录音失败，请重试。');
  }

  async function finish(op, blob) {
    release(op);
    if (active !== op) return;
    show('waiting', op.mode === 'dictation' ? '正在整理转写…' : '正在识别你的意图…');
    try {
      if (!blob.size) throw new Error('录音太短，请重试。');
      const audio = await audioPayload(blob);
      if (active !== op) return;
      const result = await post('opening-mic', {mode: op.mode, audio}, 70000);
      if (active !== op) return;
      if (result.route === 'SEND_TEXT') {
        reviewWords(op, result.instruction);
      } else if (result.route === 'WAIT_TEXT') {
        start('dictation');
      } else if (result.route === 'KEEP_LISTENING') {
        if (op.mode === 'dictation' && op.preview) {
          reviewWords(op, op.preview);
          return;
        }
        if (op.mode === 'dictation') text.value = op.before;
        active = null;
        clearHighlight();
        show('idle', '没听清，请点总麦克风再试一次。');
      } else {
        const kinds = {WAIT_HUMMING:'humming', USE_HUMMING:'humming', WAIT_RHYTHM:'rhythm', USE_RHYTHM:'rhythm'};
        const kind = kinds[result.route];
        if (!kind) throw new Error('无法判断输入类型，请再试一次。');
        const label = kind === 'humming' ? '哼唱' : '拍手';
        show('handoff', '已切到' + label + '卡片，请看卡片的录音提示。');
        await receiveMusic(kind, result.route.startsWith('USE_') ? blob : null);
        if (active === op) {
          active = null;
          show('idle', '已切到' + label + '卡片。');
        }
      }
    } catch (error) { fail(op, error); }
  }

  async function start(mode) {
    const op = active = {mode, before: text.value, preview: ''};
    clearHighlight();
    if (mode === 'dictation') {
      text.classList.add('mic-target');
      text.focus();
    }
    show('opening', mode === 'dictation' ? '正在打开口述录音…' : '正在打开麦克风…');
    try {
      op.stream = await navigator.mediaDevices.getUserMedia({audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: false,
      }});
      if (active !== op) { release(op); return; }
      const recorder = op.recorder = new MediaRecorder(op.stream);
      const chunks = [];
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => finish(op, new Blob(chunks, {type: recorder.mimeType}));
      recorder.onerror = () => fail(op, new Error('录音设备中断，请重试。'));
      recorder.start();
      show('recording', mode === 'dictation' ? '正在转写，可以开始说。说完点「结束口述」。' :
        '正在录音，请说出你的打算。说完点「结束录音」。');
      if (mode === 'dictation') startCaptions(op);
      op.timer = setTimeout(() => stopRecording(op), 29000);
    } catch (error) { fail(op, error); }
  }

  function stopRecording(op) {
    if (op?.recorder?.state !== 'recording') return;
    show('stopping', '正在结束录音…');
    op.recorder.stop();
  }

  button.addEventListener('click', () => {
    if (phase === 'idle') start('intent');
    else if (phase === 'recording') stopRecording(active);
  });
  confirm.addEventListener('click', () => {
    if (!text.value.trim() || text.value.length > text.maxLength) return;
    original = null;
    clearHighlight();
    show('idle', '开场文字已确认。');
  });
  discard.addEventListener('click', () => {
    text.value = original ?? '';
    original = null;
    clearHighlight();
    show('idle', '已撤销本次转写。');
  });

  return {
    busy: () => phase !== 'idle',
    update({live, blocked, supported}) {
      row.hidden = live;
      const recording = phase === 'recording';
      button.disabled = !supported || live || blocked || !(recording || phase === 'idle');
      button.setAttribute('aria-pressed', String(recording));
      button.textContent = recording ? (active.mode === 'dictation' ? '结束口述' : '结束录音') : '🎙 总麦克风';
      if (!supported && !live) status.textContent = '此浏览器无法录音，请使用 Edge 或 Chrome。';
      review.hidden = phase !== 'review';
      confirm.disabled = !text.value.trim() || text.value.length > text.maxLength;
      if (phase === 'review' && text.value.length > text.maxLength) status.textContent = '开场文字最多 500 字，请删减后确认。';
      text.readOnly = Boolean(active);
    },
    reset() {
      const op = active;
      active = null;
      if (op) {
        if (op.mode === 'dictation') text.value = op.before;
        if (op.recorder?.state === 'recording') op.recorder.stop();
        release(op);
      }
      if (original !== null) text.value = original;
      original = null;
      clearHighlight();
      show('idle', '说说你想哼唱、拍手，还是给乐队提要求。');
    },
  };
}
