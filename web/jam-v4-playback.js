import {MRT2_RATE, readEncodedMrt2Bar, readMrt2Bar, stereoPcmBuffer} from './jam-mrt2-stream.js';
import {pcmWave, StreamRecording, waveHeader} from './jam-recording.js';

const retainedSeconds = 600;
const streamStartFrames = Math.round(1.6 * MRT2_RATE);
const streamScheduleAhead = .32;
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
    this.confirmedBar = -1;
    this.epoch = null;
    this.streamEnd = 0;
    this.streamQueue = Promise.resolve();
    this.streamReady = [];
    this.streamTimer = null;
    this.gaps = 0;
    this.boundaryGaps = 0;
  }

  attach(view, {opus, lossless} = {}) {
    this.id = view.session;
    this.rate = view.sample_rate;
    if (['mrt2_stream', 'mrt2_direct'].includes(view.renderer)) this.stream = new StreamRecording();
    if (this.stream) {
      let lastInitial = view.next;
      while (lastInitial?.following) lastInitial = lastInitial.following;
      this.initialStreamBar = lastInitial?.bar;
      const audio = document.createElement('audio');
      this.streamFormat = opus && audio.canPlayType('audio/ogg; codecs="opus"') ? 'opus'
        : lossless && audio.canPlayType('audio/flac') ? 'flac' : null;
      if (this.streamFormat) this.streamDecoder = new OfflineAudioContext(2, 1, MRT2_RATE);
    }
  }

  setVolume(value) {
    const at = this.clock.currentTime;
    this.output.gain.setTargetAtTime(value, at, .015);
    this.volumeChanges.push({at, value});
  }

  schedule(buffer, planned, boundary = true) {
    const now = this.clock.currentTime;
    this.epoch ??= now + .12 - planned;
    let at = this.epoch + planned;
    if (at < now + .008) {
      this.gaps++;
      if (boundary) this.boundaryGaps++;
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

  scheduleStream() {
    if (this.abort.signal.aborted || this.streamTimer !== null || this.streamReady.length === 0) return;
    if (this.epoch === null) {
      const last = this.streamReady.at(-1);
      const pendingFrames = last.offset + last.pcm.byteLength / 4;
      if (last.packet.bar < this.initialStreamBar) return;
      if (pendingFrames < Math.min(streamStartFrames, last.packet.pcm_frames)) return;
    }
    try {
      while (this.streamReady.length) {
        const {packet, pcm, offset} = this.streamReady[0];
        const planned = (Math.round(packet.start * MRT2_RATE / this.rate) + offset) / MRT2_RATE;
        if (this.epoch !== null && this.epoch + planned - this.clock.currentTime > streamScheduleAhead) {
          this.streamTimer = setTimeout(() => {
            this.streamTimer = null;
            this.scheduleStream();
          }, 20);
          return;
        }
        this.streamReady.shift();
        const buffer = stereoPcmBuffer(this.clock, pcm);
        const at = this.schedule(buffer, planned, offset === 0);
        this.streamEnd = at + buffer.duration;
        if (offset === 0) this.markers.push({at, bar: packet.bar});
        this.markersStart ??= at;
        if (at - this.markersStart < retainedSeconds) this.stream.append(packet.bar, at, pcm);
      }
    } catch (error) {
      this.onStreamError(error);
    }
  }

  async enqueue(packet) {
    if (!packet || packet.bar <= this.buffered || this.abort.signal.aborted) return;
    const start = packet.start / this.rate;
    if (this.stream) {
      this.buffered = packet.bar;
      this.streamQueue = this.streamQueue.then(async () => {
        if (this.abort.signal.aborted) return;
        const signal = AbortSignal.any([this.abort.signal, AbortSignal.timeout(45_000)]);
        const receive = (pcm, offset) => {
          if (signal.aborted) return;
          this.streamReady.push({packet, pcm, offset});
          this.scheduleStream();
        };
        if (this.streamDecoder) {
          receive(await readEncodedMrt2Bar(this.id, packet, signal, this.streamDecoder, this.streamFormat), 0);
        } else {
          await readMrt2Bar(this.id, packet, signal, receive);
        }
      }).catch(error => {
        if (!this.abort.signal.aborted) this.onStreamError(error);
      });
      if (packet.following) await this.enqueue(packet.following);
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
    while (this.markers.length > 1 && this.markers[1].bar <= this.confirmedBar) this.markers.shift();
    const marker = this.markers.find(item => item.bar === this.confirmedBar + 1);
    this.markersStart ??= marker?.at;
    const time = this.stream ? Math.min(now, this.streamEnd) : now;
    return {
      session: this.id, buffered: this.buffered,
      started: marker?.at <= now ? marker.bar : this.confirmedBar,
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
    clearTimeout(this.streamTimer);
    this.streamReady.length = 0;
    return this.clock.close().catch(() => {});
  }
}
