const MIDI_TICKS_PER_SECOND = 48000;

export function midiVelocity(accent) {
  // Python's MIDI export rounds half values to the nearest even integer.
  const level = accent * 127;
  const lower = Math.floor(level);
  return Math.max(1, level - lower === .5 ? lower + lower % 2 : Math.round(level));
}

function midiTick(seconds) {
  const tick = seconds * MIDI_TICKS_PER_SECOND;
  const lower = Math.floor(tick);
  return tick - lower === .5 ? lower + lower % 2 : Math.round(tick);
}

export function createRhythmEditor(initial, nextId = () => crypto.randomUUID()) {
  if (initial.schema_version !== '1.3.0' || initial.event_type !== 'rhythm_seed')
    throw new Error('服务器未返回自由节奏数据。请重启工作台后重试。');

  let event = initial;
  const history = [];
  const duration = initial.payload.audio_duration_s;
  const lastTick = midiTick(duration) - 1;
  const onsetTick = time => Math.min(lastTick, midiTick(time));

  function revise(rhythm) {
    event = {...event, event_id: nextId(), payload: {
      ...event.payload, status: rhythm.onset_seconds.length ? 'ready' : 'no_onsets', rhythm,
    }};
    return event;
  }

  function commit(rhythm) {
    history.push(event.payload.rhythm);
    return revise(rhythm);
  }

  function checkIndex(index, length) {
    if (!Number.isInteger(index) || index < 0 || index >= length)
      throw new RangeError('请先选择一个拍点。');
  }

  return {
    get event() { return event; },
    get canUndo() { return history.length > 0; },
    save(index, onset, velocity) {
      const {onset_seconds: times, accents} = event.payload.rhythm;
      if (index !== null) checkIndex(index, times.length);
      if (!Number.isFinite(onset) || onset < 0 || onset >= duration)
        throw new RangeError('起音时间须在录音范围内，并早于录音结束。');
      if ((index === null || velocity !== undefined) &&
          (!Number.isInteger(velocity) || velocity < 1 || velocity > 127))
        throw new RangeError('相对力度须为 1–127 的整数。');
      if (times.some((time, i) => i !== index && onsetTick(time) === onsetTick(onset)))
        throw new RangeError('这个位置与已有拍点太近，请选择其他时间。');
      const keepAccent = index !== null && (velocity === undefined || velocity === midiVelocity(accents[index]));
      const accent = keepAccent ? accents[index] : velocity / 127;
      if (times[index] === onset && accents[index] === accent) return event;
      const hits = times.flatMap((time, i) => i === index ? [] : [{time, accent: accents[i]}]);
      hits.push({time: onset, accent});
      hits.sort((a, b) => a.time - b.time);
      return commit({onset_seconds: hits.map(hit => hit.time), accents: hits.map(hit => hit.accent)});
    },
    remove(index) {
      const {onset_seconds: times, accents} = event.payload.rhythm;
      checkIndex(index, times.length);
      return commit({onset_seconds: times.filter((_, i) => i !== index), accents: accents.filter((_, i) => i !== index)});
    },
    undo() {
      return history.length ? revise(history.pop()) : event;
    },
  };
}

export function playbackSchedule(midi, position) {
  const startTick = Math.min(midiTick(position), midiTick(midi.duration_s) - 1);
  const hits = midi.hits.filter(hit => midiTick(hit.onset_s) >= startTick)
    .map(hit => ({offset: Math.max(0, hit.onset_s - position), duration: hit.duration_s, velocity: hit.velocity}));
  return {duration: midi.duration_s - position, hits};
}

// Cancellation also guards continuations if a response already arrived before abort.
export function latestRequest() {
  let active;
  function cancel() {
    active?.abort();
    active = null;
  }
  return {
    cancel,
    async run(request, publish, fail) {
      cancel();
      const controller = new AbortController();
      active = controller;
      try {
        const result = await request(controller.signal);
        if (active === controller) publish(result);
      } catch (error) {
        if (active === controller) fail(error);
      } finally {
        if (active === controller) active = null;
      }
    },
  };
}
