import assert from 'node:assert/strict';
import test from 'node:test';
import {captureWindow, MAX_CAPTURE_SECONDS, pcmWave} from '../web/rhythm-capture.js';

function audio(length, sampleRate = 8000) {
  const samples = new Float32Array(length);
  return {length, duration: length / sampleRate, sampleRate, numberOfChannels: 1,
    getChannelData: () => samples};
}

test('a full supported take and a manually stopped take retain the decoded source', () => {
  for (const seconds of [.5, 25, 30]) {
    const source = audio(seconds * 8000);
    const capture = captureWindow(source, {microphone: true});
    assert.equal(capture.audio, source);
    assert.equal(capture.capped, false);
  }
  assert.equal(MAX_CAPTURE_SECONDS, 30);
});

test('microphone block overshoot caps the original and analyzed WAV at the exact last sample', () => {
  const source = audio(240153);
  const samples = source.getChannelData(0);
  samples[0] = .5;
  samples[239999] = -.25;
  samples[240000] = 1;
  const capture = captureWindow(source, {microphone: true});
  assert.equal(capture.capped, true);
  assert.equal(capture.audio.duration, 30);
  const wav = new DataView(pcmWave(capture.audio));
  assert.equal(wav.getUint32(40, true), 240000 * 2);
  assert.equal(wav.byteLength, 44 + 240000 * 2);
  assert.equal(wav.getInt16(44, true), 16384);
  assert.equal(wav.getInt16(wav.byteLength - 2, true), -8192);
  assert.equal(samples.length, 240153);
});

test('oversize imports and too-short recordings remain validation errors', () => {
  assert.throws(() => captureWindow(audio(240001)), /0.5–30/);
  assert.throws(() => captureWindow(audio(3999), {microphone: true}), /0.5–30/);
});
