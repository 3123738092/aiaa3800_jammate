import {measureAudio, monoResample, prepareSamples} from './optimized/src/audio-pipeline.js';
import {createHummingClient} from './optimized/src/humming-client.js';

const humming = createHummingClient(new URL('./optimized/dist/humming-worker.js', import.meta.url));

export async function analyzeJamHumming(buffer, {start = 0, end = buffer.length, name} = {}) {
  const durationSec = (end - start) / buffer.sampleRate;
  if (durationSec < .5) throw new Error('录音太短了，请再哼一小句。');
  const samples = await monoResample(buffer, start, end);
  const channels = Array.from({length: buffer.numberOfChannels},
    (_, channel) => buffer.getChannelData(channel).subarray(start, end));
  const preparation = prepareSamples(samples, {sourceMetrics: measureAudio(channels)});
  if (preparation.metadata.quality.status === 'rejected') {
    throw new Error(preparation.metadata.quality.warnings[0].message);
  }
  const source = {id: crypto.randomUUID(), kind: 'recording', name, durationSec,
    decodedSampleRateHz: buffer.sampleRate, decodedChannels: buffer.numberOfChannels};
  return humming.transcribe({source, ...preparation});
}
