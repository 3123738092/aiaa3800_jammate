import {modelVelocity} from './melody.js';

const field = id => document.getElementById(id);
const timeText = value => String(Number(value.toFixed(6)));

export function attachNoteControls({getEditor, getDuration, noteName, onChange, onSelect}) {
  const select = field('note-select'), dialog = field('note-dialog');
  const start = field('note-start'), end = field('note-end');
  const pitch = field('note-pitch'), velocity = field('note-velocity');
  let editingId = null;
  for (let midi = 0; midi <= 127; midi++) pitch.add(new Option(noteName(midi),String(midi)));

  function refresh(preferred = select.value) {
    const editor = getEditor(), notes = editor?.result.notes ?? [];
    select.replaceChildren(...notes.map((n,i) => new Option(
      `${i+1}. ${noteName(n.pitchMidi)} · ${n.startSec.toFixed(3)}–${(n.startSec+n.durationSec).toFixed(3)} s`,n.id)));
    if (notes.some(n=>n.id === preferred)) select.value = preferred;
    if (!notes.length) select.add(new Option('暂无音符',''));
    select.disabled = field('edit-note').disabled = !notes.length;
    field('undo-note').disabled = !editor?.canUndo;
    field('add-note').disabled = !editor;
  }
  function open(id) {
    const editor = getEditor();
    if (!editor) return;
    const note = editor.result.notes.find(n=>n.id === id);
    if (id !== null && !note) return;
    if (note) { select.value = id; onSelect(); }
    editingId = id;
    const selected = editor.result.notes.find(n=>n.id === select.value);
    const duration = getDuration();
    const position = note?.startSec ?? Math.min(selected ? selected.startSec + selected.durationSec : 0, Math.max(0,duration-.2));
    start.value = timeText(position);
    end.value = timeText(note ? note.startSec + note.durationSec : Math.min(duration,position+.2));
    start.max = end.max = timeText(duration);
    pitch.value = String(note?.pitchMidi ?? selected?.pitchMidi ?? 60);
    velocity.value = String(note ? modelVelocity(note) : 90);
    field('note-title').textContent = note ? '编辑音符' : '添加音符';
    field('remove-note').hidden = !note;
    field('note-error').textContent = '';
    dialog.showModal();
  }
  select.addEventListener('change',onSelect);
  field('edit-note').onclick = () => open(select.value);
  field('add-note').onclick = () => open(null);
  field('cancel-note').onclick = () => dialog.close();
  field('note-form').onsubmit = event => {
    event.preventDefault();
    try {
      const id = getEditor().save(editingId,{startSec:start.valueAsNumber,endSec:end.valueAsNumber,
        pitchMidi:Number(pitch.value),velocity:velocity.valueAsNumber});
      dialog.close();
      onChange(id);
    } catch (error) { field('note-error').textContent = error.message; }
  };
  field('remove-note').onclick = () => {
    getEditor().remove(editingId);
    dialog.close();
    onChange();
  };
  field('undo-note').onclick = () => { getEditor().undo(); onChange(); };
  return {refresh,open,get selectedId() { return select.value; }};
}
