import test from 'node:test';import assert from 'node:assert/strict';
import {decodeHumming} from '../src/continuity.js';
const dt=.01;
function fixture(pitches,{silence=[],attacks=[]}={}){
  const frames=pitches.map(p=>{const r=Array(88).fill(0);if(p!==null)r[p-21]=.8;return r;});
  const onsets=frames.map(()=>Array(88).fill(0));for(const [i,p]of attacks)onsets[i][p-21]=.9;
  const rms=pitches.map((_,i)=>silence.includes(i)?0:.1);
  const times=Array.from({length:frames.length+1},(_,i)=>i*dt);
  return {frames,onsets,times,rms};
}
function run(pitches,options={}){const f=fixture(pitches,options);return decodeHumming(f.frames,f.onsets,f.times,{durationSec:pitches.length*dt,rms:f.rms});}
test('continuous long note remains one note despite repeated model onset peaks',()=>{
  const pitches=Array(400).fill(60),f=fixture(pitches,{attacks:[[0,60],[40,60],[80,60],[120,60],[200,60]]});
  const r=decodeHumming(f.frames,f.onsets,f.times,{durationSec:4,rms:f.rms});assert.equal(r.notes.length,1);assert.equal(r.notes[0].durationSec,4);
});
test('semitone vibrato and short dropouts do not fragment one sustained note',()=>{
  const p=Array.from({length:400},(_,i)=>i%40>30?61:60);p.fill(null,180,185);
  const r=run(p);assert.equal(r.notes.length,1);assert.equal(r.notes[0].pitchMidi,60);assert.equal(r.notes[0].durationSec,4);
});
test('stable pitch changes and true octave changes survive',()=>{const r=run([...Array(50).fill(60),...Array(50).fill(62),...Array(50).fill(74)]);assert.deepEqual(r.notes.map(n=>n.pitchMidi),[60,62,74]);assert.deepEqual(r.notes.map(n=>n.startSec),[0,.5,1]);});
test('real silence splits even same-pitch singing',()=>{const p=[...Array(50).fill(60),...Array(8).fill(null),...Array(50).fill(60)];const r=run(p,{silence:Array.from({length:8},(_,i)=>i+50)});assert.equal(r.notes.length,2);assert.equal(r.notes[0].durationSec,.5);assert.equal(r.notes[1].startSec,.58);});
test('clearly articulated short notes survive',()=>{const p=[...Array(20).fill(60),...Array(10).fill(62),...Array(20).fill(64)];const r=run(p,{attacks:[[20,62],[30,64]]});assert.deepEqual(r.notes.map(n=>n.pitchMidi),[60,62,64]);});

test('articulated short changes survive overlapping activation from the previous pitch',()=>{
  const f=fixture([...Array(30).fill(60),...Array(10).fill(62),...Array(30).fill(64)],{attacks:[[30,62],[40,64]]});
  for(let i=30;i<40;i++)f.frames[i][60-21]=.72;
  for(let i=40;i<50;i++)f.frames[i][62-21]=.72;
  const r=decodeHumming(f.frames,f.onsets,f.times,{durationSec:.7,rms:f.rms});
  assert.deepEqual(r.notes.map(n=>n.pitchMidi),[60,62,64]);
  assert.deepEqual(r.notes.map(n=>n.startSec),[0,.3,.4]);
});
test('silence cannot create notes',()=>assert.equal(run(Array(300).fill(null)).notes.length,0));
test('original timing offset and raw source are preserved',()=>{const f=fixture(Array(100).fill(60)),raw=[{id:'a',startSec:.8,durationSec:1,pitchMidi:60}];const before=structuredClone(raw);const r=decodeHumming(f.frames,f.onsets,f.times,{durationSec:2,offsetSec:.8,rms:f.rms,raw});assert.equal(r.notes[0].startSec,.8);assert.equal(r.notes[0].durationSec,1);assert.deepEqual(raw,before);assert.deepEqual(r.notes[0].sourceNoteIds,['a']);});
