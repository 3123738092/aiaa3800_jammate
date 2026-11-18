import test from 'node:test';
import assert from 'node:assert/strict';
import midiPackage from '@tonejs/midi';
import {filterHummingArtifacts} from '../src/humming-cleanup.js';
import {buildExports,createMelodyDocument} from '../src/melody.js';
import {hummingEvent} from '../src/input-event.js';

const note=(id,start,duration,activation,onset=.2,pitch=60)=>({id,pitchMidi:pitch,
  startSec:start,durationSec:duration,velocity01:90/127,modelActivation:activation,
  onsetActivation:onset,sourceNoteIds:[id]});
const clean=notes=>filterHummingArtifacts({notes,edits:[],policy:{}});
const surrounding=middle=>[note('a',0,.5,.7),middle,note('c',.65,.5,.6,.2,64)];

test('a weak short artifact is removed without stretching adjacent notes or mutating evidence',()=>{
  const input=surrounding(note('b',.5,.15,.2,.3,62)),snapshot=structuredClone(input);
  const result=clean(input);
  assert.deepEqual(result.notes.map(n=>[n.id,n.startSec,n.durationSec]),[['a',0,.5],['c',.65,.5]]);
  assert.deepEqual(input,snapshot);
  assert.deepEqual(result.removedNotes,[input[1]]);
  assert.equal(result.edits[0].sourceId,'b');
});
test('intentional quick notes survive with a clear onset OR strong pitch support',()=>{
  for(const [activation,onset] of [[.2,.9],[.8,.1]]){
    assert.deepEqual(clean(surrounding(note('b',.5,.15,activation,onset,62))).notes.map(n=>n.id),['a','b','c']);
  }
});
test('uniformly quiet phrases, unknown evidence and long weak notes are not deleted',()=>{
  const quiet=[note('a',0,.5,.27),note('b',.5,.15,.22),note('c',.65,.5,.28)];
  assert.equal(clean(quiet).notes.length,3);
  for(const middle of [note('b',.5,.15,null),note('b',.5,.15,.2,null),note('b',.5,.3,.2)])
    assert.equal(clean(surrounding(middle)).notes.length,3);
});
test('a distant loud phrase cannot suppress a later quiet short note',()=>{
  assert.equal(clean([note('a',0,.5,.9),note('b',3,.15,.2)]).notes.length,2);
});
test('short-note runs and empty results do not need a fabricated anchor',()=>{
  assert.equal(clean([note('a',0,.1,.2),note('b',.1,.1,.2),note('c',.2,.1,.2)]).notes.length,3);
  assert.deepEqual(clean([]).notes,[]);
});
test('MIDI playback and canonical InputEvent consume the same filtered notes with original timing',()=>{
  const raw=surrounding(note('b',.5,.15,.2,.3,62));
  const exported=buildExports(createMelodyDocument({id:'test',durationSec:1.5},{},raw,clean(raw),0));
  const event=hummingEvent(exported.document,{sessionId:'test',eventId:'e'});
  const midi=new midiPackage.Midi(exported.bytes).tracks[0].notes;
  assert.deepEqual(event.payload.melody.notes.map(n=>n.id),['a','c']);
  assert.deepEqual(midi.map(n=>n.midi),[60,64]);
  assert.equal(midi[1].time,.65);
  assert.deepEqual(exported.playbackNotes,midi);
  assert.deepEqual(event.payload.melody.results.raw.notes,raw);
  assert.equal(event.confidence,null);
});
