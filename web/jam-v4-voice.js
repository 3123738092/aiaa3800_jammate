import {VoiceSegmentation} from './jam-voice-segmentation.js';

const cueUrl = new URL('assets/studio/wake-ack-zh.wav', import.meta.url);

function recorderInput(stream, onPhrase, onActivity) {
  const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus']
    .find(type => MediaRecorder.isTypeSupported(type));
  const recorder = new MediaRecorder(stream, mimeType ? {mimeType} : undefined);
  const decoder = new OfflineAudioContext(1, 1, 16000);
  const cue = new Audio(cueUrl);
  const samples = new Uint8Array(512).fill(128);
  let closed = false, timer, clip, decoding = Promise.resolve();
  const voice = new VoiceSegmentation(decoder.sampleRate, true, event => {
    if (event.type === 'start') onActivity();
    else onPhrase(event);
  });
  // Complete clips can be decoded without starting a speaker AudioContext.
  function start() {
    clip = [];
    recorder.start();
    timer = setTimeout(() => {
      if (recorder.state === 'recording') recorder.stop();
    }, 720);
  }
  recorder.ondataavailable = ({data}) => { if (data.size) clip.push(data); };
  recorder.onstop = () => {
    if (closed) return;
    const chunk = new Blob(clip, {type: recorder.mimeType});
    start();
    if (!chunk.size || !cue.paused) return;
    decoding = decoding.then(async () => {
      const pcm = (await decoder.decodeAudioData(await chunk.arrayBuffer())).getChannelData(0);
      if (closed || !cue.paused) return;
      for (let i = 0; i < samples.length; i++) samples[i] = 128 + Math.round(pcm[i] * 127);
      voice.push(pcm);
    }).catch(() => {});
  };
  start();
  return {
    stream,
    analyser: {fftSize: samples.length, getByteTimeDomainData: target => target.set(samples)},
    async acknowledge() {
      cue.currentTime = 0;
      await cue.play();
      if (closed || cue.ended) return !closed;
      return new Promise(resolve => {
        const ended = () => {
          cue.removeEventListener('ended', ended);
          cue.removeEventListener('pause', ended);
          resolve(!closed);
        };
        cue.addEventListener('ended', ended);
        cue.addEventListener('pause', ended);
      });
    },
    close() {
      closed = true;
      cue.pause();
      clearTimeout(timer);
      if (recorder.state !== 'inactive') recorder.stop();
      stream.getTracks().forEach(track => track.stop());
    },
  };
}

export async function openWakeInput(ensureAudio, onPhrase, onActivity) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1},
  });
  let context;
  let deadline;
  try {
    context = await Promise.race([
      ensureAudio(),
      new Promise((_, reject) => { deadline = setTimeout(() => reject(
        new DOMException('浏览器需要一次页面交互才能开始播报', 'PlaybackGestureRequired')), 1500); }),
    ]);
    await context.audioWorklet.addModule(new URL('jam-voice-processor.js', import.meta.url));
  } catch (error) {
    if (window.MediaRecorder && window.OfflineAudioContext) {
      try { return recorderInput(stream, onPhrase, onActivity); }
      catch { /* Release the granted tracks if both capture paths fail. */ }
    }
    stream.getTracks().forEach(track => track.stop());
    throw error;
  } finally {
    clearTimeout(deadline);
  }
  const cue = fetch(cueUrl)
    .then(response => {
      if (!response.ok) throw new Error('唤醒回应音频无法加载');
      return response.arrayBuffer();
    })
    .then(bytes => context.decodeAudioData(bytes))
    .catch(() => null);
  let source, gate, analyser, processor, reply, closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    reply?.stop();
    stream.getTracks().forEach(track => track.stop());
    source?.disconnect();
    gate?.disconnect();
    analyser?.disconnect();
    if (processor) {
      processor.port.onmessage = null;
      processor.port.close();
      processor.disconnect();
    }
  };
  async function acknowledge() {
    const buffer = await cue;
    if (closed || !buffer) return false;
    if (context.state !== 'running') return false;
    const at = context.currentTime;
    const node = context.createBufferSource();
    node.buffer = buffer;
    node.connect(context.destination);
    // Keep the spoken reply out of the next microphone segment.
    gate.gain.setValueAtTime(0, at);
    gate.gain.setValueAtTime(1, at + buffer.duration + .08);
    reply = node;
    return new Promise(resolve => {
      node.onended = () => {
        reply = null;
        node.disconnect();
        resolve(!closed);
      };
      node.start(at);
    });
  }
  try {
    source = context.createMediaStreamSource(stream);
    gate = context.createGain();
    analyser = context.createAnalyser();
    analyser.fftSize = 512;
    processor = new AudioWorkletNode(context, 'jam-voice', {
      channelCount: 1, processorOptions: {speechOnly: true},
    });
    // The worklet only emits silence to the graph.
    source.connect(gate);
    gate.connect(analyser);
    analyser.connect(processor);
    processor.connect(context.destination);
    processor.port.onmessage = ({data}) => {
      if (data.type === 'start') onActivity();
      else if (data.type === 'end') onPhrase(data);
    };
    return {stream, analyser, acknowledge, close};
  } catch (error) {
    close();
    throw error;
  }
}
