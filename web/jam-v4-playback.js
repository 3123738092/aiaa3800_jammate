import {MRT2_RATE, readMrt2Bar, stereoPcmBuffer} from './jam-mrt2-stream.js';
import {pcmWave, StreamRecording, waveHeader} from './jam-recording.js';

const retainedSeconds = 600;
export const decodeBase64 = value => Uint8Array.from(atob(value), character => character.charCodeAt(0));

export function wave(samples, rate, channels = 1) {
  return new Blob([waveHeader(rate, channels, samples.length / channels), samples], {type: 'audio/wav'});
}

export class StudioPlayback {
  constructor(clock, volume, onStreamError) {
    this.clock = clock;
    this.output = clock.createGain();
    this.output.gain.value = volume;
    this.volumeChanges = [{at: clock.currentTime, value: volume}];
    this.output.connect(clock.destination);
    this.onStreamError = onStreamError;
    this.abort = new AbortController();
    this.markers = [];
    this.recorded = [];
    this.buffered = -1;
    this.epoch = null;
    this.streamEnd = 0;
    this.streamQueue = Promise.resolve();
    this.gaps = 0;
  }

  attach(view) {
    this.id = view.session;
    this.rate = view.sample_rate;
    if (['mrt2_stream', 'mrt2_direct'].includes(view.renderer)) this.stream = new StreamRecording();
  }

  setVolume(value) {
    const at = this.clock.currentTime;
    this.output.gain.setTargetAtTime(value, at, .015);
    this.volumeChanges.push({at, value});
  }

  schedule(buffer, planned) {
    const now = this.clock.currentTime;
    this.epoch ??= now + .12 - planned;
    let at = this.epoch + planned;
    if (at < now + .008) {
      this.gaps++;
      at = now + .05;
      this.epoch = at - planned;
    }
    const node = this.clock.createBufferSource();
    node.buffer = buffer;
    node.connect(this.output);
    node.onended = () => node.disconnect();
    node.start(at);
    return at;
  }

  async enqueue(packet) {
    if (!packet || packet.bar <= this.buffered || this.abort.signal.aborted) return;
    const start = packet.start / this.rate;
    if (this.stream) {
      this.buffered = packet.bar;
      this.streamQueue = this.streamQueue.then(() => readMrt2Bar(
        this.id, packet, this.abort.signal, async (pcm, offset) => {
          const planned = start + offset / MRT2_RATE;
          while (!this.abort.signal.aborted && this.epoch !== null
                 && this.epoch + planned - this.clock.currentTime > .32) {
            await new Promise(resolve => setTimeout(resolve, 20));
          }
          if (this.abort.signal.aborted) return;
          const buffer = stereoPcmBuffer(this.clock, pcm);
          const at = this.schedule(buffer, planned);
          this.streamEnd = at + buffer.duration;
          if (!offset) this.markers.push({at, bar: packet.bar});
          this.markersStart ??= at;
          // Keep the export window bounded across long sessions.
          if (at - this.markersStart < retainedSeconds) this.stream.append(packet.bar, at, pcm);
        }
      )).catch(error => {
        if (!this.abort.signal.aborted) this.onStreamError(error);
      });
      return;
    }
    const buffer = await this.clock.decodeAudioData(decodeBase64(packet.wav).buffer);
    if (this.abort.signal.aborted) return;
    const at = this.schedule(buffer, start);
    this.markers.push({at, bar: packet.bar});
    this.markersStart ??= at;
    if (at - this.markersStart < retainedSeconds) this.recorded.push({at, buffer});
    this.buffered = packet.bar;
  }

  position() {
    const now = this.clock.currentTime;
    while (this.markers.length > 1 && this.markers[1].at <= now) this.markers.shift();
    const marker = this.markers[0];
    this.markersStart ??= marker?.at;
    const time = this.stream ? Math.min(now, this.streamEnd) : now;
    return {
      session: this.id, buffered: this.buffered,
      started: marker?.at <= now ? marker.bar : -1,
      sample: this.epoch === null ? 0 : Math.max(0, Math.floor((time - this.epoch) * this.rate)),
    };
  }

  async export() {
    const now = this.clock.currentTime;
    if (this.stream) {
      const take = this.stream.snapshot(now);
      if (take) return pcmWave(take);
    }
    const played = this.recorded.filter(item => item.at < now);
    if (!played.length) throw new Error('尚无已播放的演奏。');
    const start = played[0].at;
    const last = played.at(-1);
    const rate = this.clock.sampleRate;
    const frames = Math.max(1, Math.ceil((Math.min(now, last.at + last.buffer.duration) - start) * rate));
    const offline = new OfflineAudioContext(2, frames, rate);
    const master = offline.createGain();
    master.connect(offline.destination);
    for (const point of this.volumeChanges) {
      if (point.at <= start) master.gain.value = point.value;
      else if (point.at < now) master.gain.setTargetAtTime(point.value, point.at - start, .015);
    }
    for (const {at, buffer} of played) {
      const node = offline.createBufferSource();
      node.buffer = buffer;
      node.connect(master);
      node.start(at - start);
    }
    const rendered = await offline.startRendering();
    let peak = 0;
    for (let c = 0; c < 2; c++) {
      for (const sample of rendered.getChannelData(c)) peak = Math.max(peak, Math.abs(sample));
    }
    const pcm = new Int16Array(frames * 2);
    const scale = 32767 * (peak > .99 ? .99 / peak : 1);
    for (let c = 0; c < 2; c++) {
      const samples = rendered.getChannelData(c);
      for (let i = 0; i < frames; i++) pcm[i * 2 + c] = Math.round(samples[i] * scale);
    }
    return wave(pcm, rate, 2);
  }

  close() {
    this.abort.abort();
    return this.clock.close().catch(() => {});
  }
}
