import assert from 'node:assert/strict';
import test from 'node:test';
import {createRhythmEditor, latestRequest, playbackSchedule} from '../web/rhythm-editor.js';

const original = {
  schema_version: '1.3.0', event_id: 'detection', session_id: 'take',
  source: 'rhythm', event_type: 'rhythm_seed', started_at: null, ended_at: null, confidence: null,
  payload: {
    status: 'ready', audio_duration_s: 7.15,
    rhythm: {onset_seconds: [.18, 2.1, 5.375], accents: [.5719, .29, .92]},
    raw_rhythm: {onset_seconds: [.18, 2.1, 5.375], accents: [.5719, .29, .92]},
  },
};

function editor(event = original) {
  let revision = 0;
  return createRhythmEditor(structuredClone(event), () => `edit-${++revision}`);
}

test('moving across hits reorders strengths without changing the raw event or take duration', () => {
  const model = editor();
  const next = model.save(0, 3.6191);
  assert.equal(next.event_id, 'edit-1');
  assert.equal(next.session_id, 'take');
  assert.deepEqual(next.payload.rhythm, {
    onset_seconds: [2.1, 3.6191, 5.375], accents: [.29, .5719, .92],
  });
  assert.deepEqual(next.payload.raw_rhythm, original.payload.raw_rhythm);
  assert.equal(next.payload.audio_duration_s, 7.15);
  assert.equal(model.save(1, 3.6191, 73), next, 'saving the displayed velocity preserves the original precision');
});

test('explicit velocity edits and add/delete/undo change only the selected rhythm', () => {
  const model = editor();
  const accented = model.save(1, 2.1, 51);
  assert.equal(accented.payload.rhythm.accents[1], 51 / 127);
  const added = model.save(null, 0, 80);
  assert.deepEqual(added.payload.rhythm.onset_seconds, [0, .18, 2.1, 5.375]);
  assert.equal(added.payload.rhythm.accents[0], 80 / 127);
  const removed = model.remove(2);
  assert.deepEqual(removed.payload.rhythm.onset_seconds, [0, .18, 5.375]);
  assert.deepEqual(model.undo().payload, added.payload);
  const restored = model.undo();
  assert.deepEqual(restored.payload, accented.payload);
  assert.notEqual(restored.event_id, accented.event_id);
});

test('silence is editable and deleting its only added hit keeps duration and raw evidence', () => {
  const empty = {onset_seconds: [], accents: []};
  const silent = {...original, payload: {...original.payload, status: 'no_onsets', rhythm: empty, raw_rhythm: empty}};
  const model = editor(silent);
  assert.equal(model.undo(), model.event);
  assert.equal(model.save(null, .5, 40).payload.status, 'ready');
  assert.deepEqual(model.remove(0).payload, silent.payload);
  assert.equal(model.undo().payload.status, 'ready');
});

test('invalid, too-close and out-of-take edits leave the current event and undo intact', () => {
  const model = editor();
  for (const [onset, velocity] of [[NaN, 80], [-.01, 80], [7.15, 80], [2.1000001, 80], [1, 0], [1, 128], [1, 1.2]]) {
    assert.throws(() => model.save(null, onset, velocity), RangeError);
  }
  assert.deepEqual(model.event, original);
  assert.equal(model.canUndo, false);
});

test('preview uses returned MIDI velocities and offsets, keeping the trailing rest after seek', () => {
  const midi = {duration_s: 7.15, hits: [
    {onset_s: .21875, duration_s: .0625, velocity: 22},
    {onset_s: 2.5, duration_s: .0625, velocity: 44},
    {onset_s: 5.125, duration_s: .0625, velocity: 103},
  ]};
  assert.deepEqual(playbackSchedule(midi, 2.5), {duration: 4.65, hits: [
    {offset: 0, duration: .0625, velocity: 44},
    {offset: 2.625, duration: .0625, velocity: 103},
  ]});
  assert.deepEqual(playbackSchedule(midi, 6.15), {duration: 1, hits: []});
});

test('superseded and cancelled requests cannot publish after resolving or rejecting', async () => {
  const request = latestRequest();
  let resolveOld, signalOld, rejectCancelled;
  const publications = [];
  const publish = result => publications.push(result);
  const fail = error => publications.push(error.message);
  const old = request.run(signal => {
    signalOld = signal;
    return new Promise(resolve => { resolveOld = resolve; });
  }, publish, fail);
  await request.run(() => Promise.resolve('new MIDI'), publish, fail);
  assert.equal(signalOld.aborted, true);
  resolveOld('stale MIDI');
  await old;
  const cancelled = request.run(() => new Promise((_, reject) => { rejectCancelled = reject; }), publish, fail);
  request.cancel();
  rejectCancelled(new Error('stale error'));
  await cancelled;
  assert.deepEqual(publications, ['new MIDI']);
});
