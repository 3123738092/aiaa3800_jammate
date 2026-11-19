import test from 'node:test';
import assert from 'node:assert/strict';
import {createMelodyEditor} from '../src/melody-editor.js';
import {buildExports,createMelodyDocument} from '../src/melody.js';
import {hummingEvent} from '../src/input-event.js';

const original = {id:'d1',pitchMidi:60,startSec:.4,durationSec:.3,velocity01:90/127,
  modelActivation:.8,sourceNoteIds:['n1'],frameRange:[22,38]};
const initial = {notes:[original],edits:[],forceMonophonic:true};
const fields = {startSec:.11,endSec:.20,pitchMidi:64,velocity:45};

test('a manual 90 ms note and explicit velocity survive encoding and confirmation',()=>{
  const editor=createMelodyEditor(initial,2);
  const id=editor.save(null,fields);
  editor.save('d1',{startSec:.45,endSec:.65,pitchMidi:62,velocity:12});
  const document=createMelodyDocument({id:'sample',durationSec:2},{},[],editor.result,1);
  const out=buildExports(document,'cleaned','soft',{modelDynamics:true});
  assert.deepEqual(out.playbackNotes.map(n=>[n.midi,Math.round(n.velocity*127)]),[[64,45],[62,12]]);
  assert(Math.abs(out.playbackNotes[0].duration-.09)<.0011);
  const event=hummingEvent(out.document,{sessionId:'s',eventId:'e'});
  assert.equal(event.schema_version,'1.4.0');
  assert.equal(event.payload.melody.notes[0].id,id);
  editor.remove(id);
  assert.equal(event.payload.melody.notes.length,2);
  editor.undo(); editor.undo(); editor.undo();
  assert.strictEqual(editor.result,initial);
  assert.equal(editor.canUndo,false);
});

test('invalid edits leave history and the original audio timeline intact',()=>{
  const editor=createMelodyEditor(initial,2);
  for(const invalid of [{startSec:-.1},{endSec:.11},{endSec:2.1},{velocity:0},{pitchMidi:128},
    {pitchMidi:60,startSec:.5,endSec:.6}]) {
    assert.throws(()=>editor.save(null,{...fields,...invalid}));
    assert.strictEqual(editor.result,initial);
  }
  editor.remove('d1');
  assert.deepEqual(editor.result.notes,[]);
  assert.equal(editor.result.userEdited,true);
  editor.undo();
  assert.strictEqual(editor.result,initial);
});
