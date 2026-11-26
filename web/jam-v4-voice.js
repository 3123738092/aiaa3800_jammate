export async function openWakeInput(context, onPhrase, onActivity) {
  const module = new URL('jam-voice-processor.js', import.meta.url);
  await context.audioWorklet.addModule(module);
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1},
  });
  let source, analyser, processor;
  const close = () => {
    stream.getTracks().forEach(track => track.stop());
    source?.disconnect();
    analyser?.disconnect();
    if (processor) {
      processor.port.onmessage = null;
      processor.port.close();
      processor.disconnect();
    }
  };
  try {
    source = context.createMediaStreamSource(stream);
    analyser = context.createAnalyser();
    analyser.fftSize = 512;
    processor = new AudioWorkletNode(context, 'jam-voice', {
      channelCount: 1, processorOptions: {speechOnly: true},
    });
    // The worklet only emits silence to the graph.
    source.connect(analyser);
    analyser.connect(processor);
    processor.connect(context.destination);
    processor.port.onmessage = ({data}) => {
      if (data.type === 'start') onActivity();
      else if (data.type === 'end') onPhrase(data);
    };
    return {stream, analyser, close};
  } catch (error) {
    close();
    throw error;
  }
}
