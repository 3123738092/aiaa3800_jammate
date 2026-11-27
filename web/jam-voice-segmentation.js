export class VoiceSegmentation {
  constructor(rate, speechOnly, emit) {
    this.sampleRate = rate;
    this.speechOnly = speechOnly;
    this.emit = emit;
    this.maxFrames = this.speechOnly ? 600 : 400;
    this.frame = new Float32Array(Math.round(rate * .02));
    this.used = 0;
    this.noise = .004;
    this.preRoll = [];
    this.loud = 0;
    this.barFrames = Math.ceil(4 * 60 / 96 / .02);
    this.resetPhrase();
  }

  setTempo(bpm) {
    if (Number.isFinite(bpm) && bpm >= 30 && bpm <= 250) this.barFrames = Math.ceil(4 * 60 / bpm / .02);
  }

  resetPhrase() {
    this.capturing = false;
    this.chunks = [];
    this.frames = 0;
    this.voiced = 0;
    this.quiet = 0;
    this.steady = 0;
    this.hasSustain = false;
    this.attacks = 0;
    this.notified = false;
  }

  readFrame() {
    let power = 0;
    let peak = 0;
    for (const sample of this.frame) {
      power += sample * sample;
      peak = Math.max(peak, Math.abs(sample));
    }
    const level = Math.sqrt(power / this.frame.length);
    const audible = level > Math.max(.011, this.noise * 2.8);
    const sharp = audible && peak > .08 && peak > 2.5 * level;
    if (!this.capturing) {
      if (!audible) this.noise += (level - this.noise) * .04;
      this.preRoll.push(this.frame.slice());
      if (this.preRoll.length > 14) this.preRoll.shift();
      this.loud = audible ? this.loud + 1 : 0;
      if (this.loud >= 3 || sharp) {
        this.capturing = true;
        this.chunks = this.preRoll;
        this.frames = this.chunks.length;
        this.firstAttack = this.frames - Math.max(1, this.loud);
        this.voiced = this.steady = this.loud;
        this.attacks = 1;
        this.preRoll = [];
        this.loud = 0;
      }
      return;
    }
    this.frames += 1;
    if (this.frames <= this.maxFrames) this.chunks.push(this.frame.slice());
    else this.chunks.length = 0;
    if (audible) {
      this.voiced += 1;
      if (this.quiet >= 5) this.attacks += 1;
    }
    this.steady = audible ? this.steady + 1 : 0;
    this.hasSustain ||= this.steady >= 7;
    if (!this.notified && (this.hasSustain || this.attacks >= 2)) {
      this.notified = true;
      this.emit({type: 'start'});
    }
    this.quiet = audible ? 0 : this.quiet + 1;
    const waitingForSilence = this.speechOnly || this.hasSustain ? this.quiet < (this.speechOnly ? 32 : 34) :
      this.quiet < 60 || this.frames - this.firstAttack < this.barFrames + 2;
    if (waitingForSilence) return;
    if (this.frames > this.maxFrames) {
      this.emit({type: 'end', tooLong: true});
    } else if (this.voiced >= 8 || this.attacks >= 2) {
      const chunks = this.speechOnly || this.hasSustain ?
        this.chunks.slice(0, this.chunks.length - this.quiet + 10) : this.chunks;
      const samples = new Float32Array(chunks.length * this.frame.length);
      for (const [i, chunk] of chunks.entries()) samples.set(chunk, i * this.frame.length);
      this.emit({type: 'end', samples, sampleRate: this.sampleRate}, [samples.buffer]);
    } else {
      this.emit({type: 'end'});
    }
    this.resetPhrase();
  }

  push(channel) {
    for (const sample of channel) {
      this.frame[this.used++] = sample;
      if (this.used === this.frame.length) {
        this.readFrame();
        this.used = 0;
      }
    }
  }
}
