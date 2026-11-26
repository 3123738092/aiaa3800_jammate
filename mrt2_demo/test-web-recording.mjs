import assert from 'node:assert/strict';
import {pcmWave, StreamRecording} from '../web/jam-recording.js';

const rate = 48000;
const recording = new StreamRecording();
const pcm = (...frames) => new Uint8Array(new Int16Array(frames.flat()).buffer);
recording.append(0, 1, pcm([123, -123], [456, -456]));
recording.append(0, 1 + 2 / rate, pcm([789, -789]));
recording.append(1, 1 + 4 / rate, pcm([111, -111], [222, -222]));
const snapshot = recording.snapshot(1 + 5 / rate);
assert.equal(snapshot.pcm.length, 2);
const bytes = await pcmWave(snapshot).arrayBuffer();
const header = new DataView(bytes);
assert.equal(header.getUint32(24, true), rate);
assert.equal(header.getUint16(22, true), 2);
assert.equal(header.getUint32(40, true), 20);
assert.deepEqual([...new Int16Array(bytes, 44)], [123, -123, 456, -456, 789, -789, 0, 0, 111, -111]);
assert.equal(recording.snapshot(1), null);
console.log('PCM archive preserves stereo samples, scheduled gaps and stop-time trimming');
