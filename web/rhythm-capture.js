export const MAX_CAPTURE_SECONDS = 30;

// Keep PCM timing and amplitudes independent of the playback device's sample rate.
export function canAnalyzeOriginalWave(bytes) {
  if (bytes.byteLength < 36 || bytes.byteLength > 11 * 1024 * 1024) return false;
  const view = new DataView(bytes);
  if (view.getUint32(0) !== 0x52494646 || view.getUint32(8) !== 0x57415645) return false; // RIFF/WAVE
  for (let offset = 12; offset + 8 <= bytes.byteLength;) {
    const length = view.getUint32(offset + 4, true);
    const start = offset + 8;
    if (start + length > bytes.byteLength) return false;
    if (view.getUint32(offset) === 0x666d7420 && length >= 16) { // fmt
      const format = view.getUint16(start, true), channels = view.getUint16(start + 2, true);
      const rate = view.getUint32(start + 4, true);
      return (format === 1 || format === 3) && channels > 0 && channels <= 32 && rate >= 8000 && rate <= 96000;
    }
    offset = start + length + (length % 2);
  }
  return false;
}

export function captureWindow(audio, {microphone = false} = {}) {
  if (audio.duration < .5 || (!microphone && audio.duration > MAX_CAPTURE_SECONDS))
    throw new Error('录音需为 0.5–30 秒。');
  if (audio.duration <= MAX_CAPTURE_SECONDS) return {audio, capped: false};

  const {sampleRate, numberOfChannels} = audio;
  // MediaRecorder can deliver one or more blocks after the wall-clock limit.
  // Keep the beginning of the take and exactly 30 seconds of decoded samples.
  const length = MAX_CAPTURE_SECONDS * sampleRate;
  return {capped: true, audio: {length, duration: length / sampleRate, sampleRate, numberOfChannels,
    getChannelData: index => audio.getChannelData(index).subarray(0, length)}};
}

export function pcmWave(audio) {
  const {length, sampleRate, numberOfChannels} = audio;
  const channels = Array.from({length: numberOfChannels}, (_, index) => audio.getChannelData(index));
  const data = new ArrayBuffer(44 + length * 2), view = new DataView(data);
  function writeText(offset, text) {
    for (const [index, character] of [...text].entries()) view.setUint8(offset + index, character.charCodeAt(0));
  }
  writeText(0, 'RIFF');
  view.setUint32(4, 36 + length * 2, true);
  writeText(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, 'data');
  view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    let sample = 0;
    for (const channel of channels) sample += channel[i] / numberOfChannels;
    view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true);
  }
  return data;
}
