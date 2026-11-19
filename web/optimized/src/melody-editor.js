import {modelVelocity} from './melody.js';

const seconds = value => Math.round(value * 1e6) / 1e6;

export function createMelodyEditor(initial, duration) {
  let result = initial;
  const history = [];
  function replace(notes) {
    history.push(result);
    result = {...initial, userEdited:true, notes};
  }
  function find(id) {
    const note = result.notes.find(n => n.id === id);
    if (!note) throw new Error('请先选择一个音符。');
    return note;
  }
  return {
    get result() { return result; },
    get canUndo() { return history.length > 0; },
    save(id, {startSec, endSec, pitchMidi, velocity}) {
      const old = id === null ? null : find(id);
      if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || startSec < 0 ||
          endSec > duration + .000001 || endSec <= startSec)
        throw new Error('起止时间须在录音范围内，结束时间要晚于开始时间。');
      const start = seconds(startSec), length = seconds(endSec - startSec);
      if (Math.round(length * 960) < 1) throw new Error('音符太短，无法写入一个 MIDI 时间刻度。');
      if (!Number.isInteger(pitchMidi) || pitchMidi < 0 || pitchMidi > 127)
        throw new Error('请选择有效的 MIDI 音高。');
      if (!Number.isInteger(velocity) || velocity < 1 || velocity > 127)
        throw new Error('力度须为 1–127 的整数。');
      if (old && old.startSec === start && old.durationSec === length && old.pitchMidi === pitchMidi &&
          modelVelocity(old) === velocity) return id;
      if (result.notes.some(n => n.id !== id && n.pitchMidi === pitchMidi &&
          start < seconds(n.startSec + n.durationSec) && n.startSec < seconds(start + length)))
        throw new Error('同一音高的两个音符时间重叠，请调整起止时间。');
      const note = {id:old?.id ?? crypto.randomUUID(), manual:true, pitchMidi, startSec:start,
        durationSec:length, velocity01:velocity / 127, sourceNoteIds:old?.sourceNoteIds ?? []};
      const notes = [...result.notes.filter(n => n.id !== id),note];
      notes.sort((a,b)=>a.startSec-b.startSec || a.pitchMidi-b.pitchMidi);
      replace(notes);
      return note.id;
    },
    remove(id) {
      find(id);
      replace(result.notes.filter(n=>n.id !== id));
    },
    undo() {
      if (history.length) result = history.pop();
    },
  };
}
