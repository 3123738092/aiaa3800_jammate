export const MRT2_RATE = 48000;
const sampleBytes = 4;
const blockBytes = MRT2_RATE / 25 * sampleBytes;

export function stereoPcmBuffer(context, pcm) {
  const data = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  const frames = pcm.length / sampleBytes;
  const buffer = context.createBuffer(2, frames, MRT2_RATE);
  for (let channel = 0; channel < 2; channel++) {
    const output = buffer.getChannelData(channel);
    for (let frame = 0; frame < frames; frame++) {
      output[frame] = data.getInt16(frame * sampleBytes + channel * 2, true) / 32768;
    }
  }
  return buffer;
}

export async function readMrt2Bar(session, packet, signal, onBlock) {
  const query = new URLSearchParams({session, bar: packet.bar});
  const response = await fetch('/api/jam/mrt2-stream?' + query, {signal, cache: 'no-store'});
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(result.error ?? 'MRT2 音频流连接失败。');
  }
  const expected = packet.pcm_frames * sampleBytes;
  if (!response.body || Number(response.headers.get('Content-Length')) !== expected) {
    throw new Error('MRT2 音频流格式不匹配。');
  }
  const reader = response.body.getReader();
  let received = 0;
  let pending = new Uint8Array(Math.min(blockBytes, expected));
  let filled = 0;
  try {
    while (received < expected) {
      const {done, value} = await reader.read();
      if (done || received + value.length > expected) throw new Error('MRT2 音频流长度不匹配。');
      for (let offset = 0; offset < value.length;) {
        const size = Math.min(value.length - offset, pending.length - filled);
        pending.set(value.subarray(offset, offset + size), filled);
        offset += size;
        filled += size;
        received += size;
        if (filled === pending.length) {
          await onBlock(pending, (received - filled) / sampleBytes);
          pending = new Uint8Array(Math.min(blockBytes, expected - received));
          filled = 0;
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
