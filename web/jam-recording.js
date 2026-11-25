import {MRT2_RATE} from './jam-mrt2-stream.js';

const BYTES_PER_FRAME = 4;
const SILENCE = new Uint8Array(MRT2_RATE * BYTES_PER_FRAME);

export function waveHeader(rate, channels, frames) {
  const bytes = frames * channels * 2;
  if (bytes > 0xffffffff - 36) throw new RangeError('WAV 文件超过格式大小限制。');
  const data = new DataView(new ArrayBuffer(44));
  const tag = (offset, value) => {
    for (let i = 0; i < value.length; i++) data.setUint8(offset + i, value.charCodeAt(i));
  };
  tag(0, 'RIFF');
  data.setUint32(4, 36 + bytes, true);
  tag(8, 'WAVE');
  tag(12, 'fmt ');
  data.setUint32(16, 16, true);
  data.setUint16(20, 1, true);
  data.setUint16(22, channels, true);
  data.setUint32(24, rate, true);
  data.setUint32(28, rate * channels * 2, true);
  data.setUint16(32, channels * 2, true);
  data.setUint16(34, 16, true);
  tag(36, 'data');
  data.setUint32(40, bytes, true);
  return data.buffer;
}

function archive(segment) {
  return {at: segment.at, end: segment.at + segment.frames / MRT2_RATE, blob: new Blob(segment.blocks)};
}

export class StreamRecording {
  #segments = [];
  #pending = null;

  append(bar, at, pcm) {
    const previous = this.#pending;
    if (!previous || previous.bar !== bar
        || Math.abs(at - previous.at - previous.frames / MRT2_RATE) > .5 / MRT2_RATE) {
      if (previous) this.#segments.push(archive(previous));
      this.#pending = {bar, at, frames: 0, blocks: []};
    }
    this.#pending.blocks.push(pcm);
    this.#pending.frames += pcm.byteLength / BYTES_PER_FRAME;
  }

  snapshot(now) {
    const segments = this.#pending ? [...this.#segments, archive(this.#pending)] : this.#segments;
    const played = segments.filter(segment => segment.at < now);
    if (!played.length) return null;
    return {pcm: played, rate: MRT2_RATE, start: played[0].at, end: Math.min(now, played.at(-1).end)};
  }
}

export function pcmWave(take) {
  const frames = Math.max(1, Math.round((take.end - take.start) * take.rate));
  const parts = [waveHeader(take.rate, 2, frames)];
  let cursor = 0;
  for (const {at, blob} of take.pcm) {
    const start = Math.round((at - take.start) * take.rate);
    const limit = Math.min(frames, start + blob.size / BYTES_PER_FRAME);
    if (limit <= Math.max(start, cursor)) continue;
    while (cursor < start) {
      const gap = Math.min(start - cursor, MRT2_RATE);
      parts.push(SILENCE.subarray(0, gap * BYTES_PER_FRAME));
      cursor += gap;
    }
    parts.push(blob.slice((cursor - start) * BYTES_PER_FRAME, (limit - start) * BYTES_PER_FRAME));
    cursor = limit;
  }
  if (cursor < frames) parts.push(SILENCE.subarray(0, (frames - cursor) * BYTES_PER_FRAME));
  return new Blob(parts, {type: 'audio/wav'});
}
