export const MRT2_RATE = 48000;
// Replayable bars can be retried within the server's 90-second session lifetime.
export const MRT2_RECONNECT_MS = 60_000;
const sampleBytes = 4;
const blockBytes = MRT2_RATE / 25 * sampleBytes;

export function retryableConnection(error) {
  return ['TypeError', 'TimeoutError'].includes(error?.name)
    || [408, 429, 500, 502, 503, 504].includes(error?.status);
}

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

async function openMrt2Bar(session, bar, signal, format) {
  const query = new URLSearchParams({session, bar});
  if (format) query.set('format', format);
  const response = await fetch('/api/jam/mrt2-stream?' + query, {signal, cache: 'no-store'});
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw Object.assign(new Error(result.error ?? 'MRT2 音频流连接失败。'), {status: response.status});
  }
  return response;
}

async function downloadEncodedBar(session, bar, format, signal) {
  const response = await openMrt2Bar(session, bar, signal, format);
  const bytes = await response.arrayBuffer();
  const length = Number(response.headers.get('Content-Length'));
  if (length && bytes.byteLength !== length) throw new TypeError('MRT2 小节传输未完成。');
  return bytes;
}

// Use only with requests whose duplicate has no additional effect.
export async function requestWithReplay(request, deadline, replay) {
  const cancel = new AbortController();
  const signal = AbortSignal.any([deadline, cancel.signal]);
  let timer;
  try {
    const primary = request(signal);
    const first = await Promise.race([primary, new Promise(resolve => {
      timer = setTimeout(resolve, replay.afterMs);
    })]);
    if (first !== undefined) return first;
    if (!replay.needed()) return await primary;
    return await Promise.any([primary, request(signal)]).catch(({errors}) => {
      throw errors.find(error => !retryableConnection(error)) ?? errors[0];
    });
  } finally {
    clearTimeout(timer);
    cancel.abort();
  }
}

export async function readEncodedMrt2Bar(session, packet, signal, decoder, format, replay) {
  const reconnect = AbortSignal.any([signal, AbortSignal.timeout(MRT2_RECONNECT_MS)]);
  const download = deadline => downloadEncodedBar(session, packet.bar, format, deadline);
  let bytes;
  let retryDelay = 180;
  for (;;) {
    reconnect.throwIfAborted();
    try {
      const deadline = AbortSignal.any([reconnect, AbortSignal.timeout(10_000)]);
      bytes = await (replay ? requestWithReplay(download, deadline, replay) : download(deadline));
      break;
    } catch (error) {
      if (reconnect.aborted || !retryableConnection(error)) throw error;
      replay = undefined;
      await new Promise(resolve => setTimeout(resolve, retryDelay));
      retryDelay = Math.min(retryDelay * 2, 1250);
    }
  }
  const decoded = await decoder.decodeAudioData(bytes);
  if (decoded.length !== packet.pcm_frames || decoded.numberOfChannels !== 2 || decoded.sampleRate !== MRT2_RATE) {
    throw new Error('MRT2 小节解码后长度不匹配。');
  }
  const pcm = new Uint8Array(decoded.length * sampleBytes);
  const view = new DataView(pcm.buffer);
  for (let channel = 0; channel < 2; channel++) {
    const values = decoded.getChannelData(channel);
    for (let frame = 0; frame < decoded.length; frame++) {
      const sample = Math.min(32767, Math.max(-32768, Math.round(values[frame] * 32768)));
      view.setInt16(frame * sampleBytes + channel * 2, sample, true);
    }
  }
  return pcm;
}

export async function readMrt2Bar(session, packet, signal, onBlock) {
  const response = await openMrt2Bar(session, packet.bar, signal);
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
