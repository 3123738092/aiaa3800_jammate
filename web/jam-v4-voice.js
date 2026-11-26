export async function openWakeInput(context, onPhrase, onActivity) {
  const module = new URL('jam-voice-processor.js', import.meta.url);
  const cue = fetch(new URL('assets/studio/wake-ack-zh.wav', import.meta.url))
    .then(response => {
      if (!response.ok) throw new Error('唤醒回应音频无法加载');
      return response.arrayBuffer();
    })
    .then(bytes => context.decodeAudioData(bytes))
    .catch(() => null);
  await context.audioWorklet.addModule(module);
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1},
  });
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
