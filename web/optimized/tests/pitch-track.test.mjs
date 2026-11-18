import test from 'node:test';
import assert from 'node:assert/strict';
import {trackHummingPitch,refineHummingWithPitch} from '../src/pitch-track.js';
import {decodeHumming,frameRms} from '../src/continuity.js';
import {createMelodyDocument,buildExports} from '../src/melody.js';
import {hummingEvent} from '../src/input-event.js';
const dt=.01;
function evidence(pitches,{rms=null}={}){
  return {pitchTrack:{pitches,periodicity:pitches.map(p=>p===null?0:.9)},
    times:Array.from({length:pitches.length+1},(_,i)=>i*dt),rms:rms??pitches.map(()=>.1)};
}
const note=(id,p,a,b)=>({id,pitchMidi:p,startSec:a*dt,durationSec:(b-a)*dt,velocity01:.7,
  modelActivation:.8,onsetActivation:.8,sourceNoteIds:[id],frameRange:[a,b]});
const refine=(notes,ev)=>refineHummingWithPitch({notes,edits:[],policy:{}},ev);

test('pitch evidence tracks different voice ranges without rounding at semitone boundaries',()=>{
  const sr=22050;
  for(const pitch of [45.45,60.55,76.3]){
    const samples=Float32Array.from({length:sr/2},(_,i)=>.1*Math.sin(2*Math.PI*440*2**((pitch-69)/12)*i/sr));
    const frames=Array.from({length:50},()=>{const f=Array(88).fill(0);f[Math.round(pitch)-21]=.8;return f;});
    const times=Array.from({length:51},(_,i)=>i*dt);
    const result=trackHummingPitch(samples,sr,times,frames);
    for(const p of result.pitches.slice(10,40))assert(Math.abs(p-pitch)<.15,`${pitch} -> ${p}`);
  }
});
test('acoustic silence and unpitched noise do not become confident pitch tracks',()=>{
  const sr=22050,frames=Array.from({length:50},()=>Array(88).fill(.8)),times=Array.from({length:51},(_,i)=>i*dt);
  let seed=17;const noise=Float32Array.from({length:sr/2},()=>{seed=(seed*1664525+1013904223)>>>0;return (seed/2**32-.5)*.2;});
  for(const samples of [new Float32Array(sr/2),noise])assert(trackHummingPitch(samples,sr,times,frames).pitches.every(p=>p===null));
});
test('one continuous pitch drifting across a MIDI boundary produces one note',()=>{
  const notes=[note('a',60,0,30),note('b',61,30,60)],original=structuredClone(notes);
  const pitches=Array.from({length:60},(_,i)=>60.4+i*.003);
  const result=refine(notes,evidence(pitches));
  assert.equal(result.notes.length,1);assert.equal(result.notes[0].durationSec,.6);
  assert.deepEqual(result.notes[0].sourceNoteIds,['a','b']);assert.deepEqual(notes,original);
});
test('actual semitone steps, articulated repeats and acoustic rests remain separate',()=>{
  const notes=[note('a',60,0,30),note('b',61,30,60)];
  assert.equal(refine(notes,evidence([...Array(30).fill(60),...Array(30).fill(61)])).notes.length,2);
  for(const rms of [Array.from({length:60},(_,i)=>i>=26&&i<30?.01:.1),Array.from({length:60},(_,i)=>i>=26&&i<30?0:.1)])
    assert.equal(refine(notes,evidence([...Array(30).fill(60.4),...Array(30).fill(60.6)],{rms})).notes.length,2);
});
test('brief unstable pitch excursions merge into the surrounding sustained pitch',()=>{
  const notes=[note('a',60,0,30),note('b',59,30,42),note('c',60,42,75)];
  const pitches=[...Array(30).fill(60),...Array.from({length:12},(_,i)=>59+Math.abs(i-6)*.1),...Array(33).fill(60)];
  const result=refine(notes,evidence(pitches));
  assert.deepEqual(result.notes.map(n=>[n.pitchMidi,n.startSec,n.durationSec]),[[60,0,.75]],JSON.stringify(result.pitchEvidence));
  assert(result.edits.some(e=>e.type==='absorb_unstable_pitch_excursion'));
});
test('a stable intentional 80 ms short note survives even without a large volume attack',()=>{
  const notes=[note('a',60,0,30),note('b',59,30,38),note('c',60,38,75)];
  const result=refine(notes,evidence([...Array(30).fill(60),...Array(8).fill(59),...Array(37).fill(60)]));
  assert.deepEqual(result.notes.map(n=>n.pitchMidi),[60,59,60]);
});
test('actual PCM short notes survive acoustic windowing before refinement',()=>{
  const sr=22050;
  for(const pitch of [45,60,76])for(const length of [8,9,10]){
    const labels=[...Array(30).fill(pitch),...Array(length).fill(pitch-1),...Array(30).fill(pitch)];
    let phase=0;
    const samples=Float32Array.from({length:Math.round(labels.length*dt*sr)},(_,i)=>{
      const p=labels[Math.min(labels.length-1,Math.floor(i/sr/dt))];phase+=2*Math.PI*440*2**((p-69)/12)/sr;return .1*Math.sin(phase);
    });
    const frames=labels.map(p=>{const a=Array(88).fill(0);a[p-21]=.8;return a;});
    const onsets=labels.map((p,i)=>{const a=Array(88).fill(0);if(i===0||p!==labels[i-1])a[p-21]=.9;return a;});
    const times=Array.from({length:labels.length+1},(_,i)=>i*dt),rms=frameRms(samples,sr,times);
    const decoded=decodeHumming(frames,onsets,times,{durationSec:labels.length*dt,rms});
    const pitchTrack=trackHummingPitch(samples,sr,times,frames);
    const result=refineHummingWithPitch(decoded,{pitchTrack,times,rms});
    assert.deepEqual(result.notes.map(n=>n.pitchMidi),[pitch,pitch-1,pitch],`${pitch}, ${length*10} ms`);
  }
});
test('unknown pitch evidence cannot erase notes or join notes across a rest',()=>{
  const notes=[note('a',60,0,30),note('b',61,30,60)];
  assert.deepEqual(refine(notes,evidence(Array(60).fill(null))).notes,notes);
  assert.deepEqual(refine([],evidence([])).notes,[]);
});
test('neighbor merging cannot accumulate a slow multi-semitone drift into one pitch',()=>{
  const notes=[note('a',60,0,30),note('b',61,30,60),note('c',61,60,90),note('d',62,90,120)];
  const result=refine(notes,evidence([60.4,60.7,61.0,61.3].flatMap(p=>Array(30).fill(p))));
  assert(result.notes.length>=2);
});
test('acoustically merged notes reach MIDI and InputEvent together, retaining raw evidence',()=>{
  const raw=[note('a',60,0,30),note('b',61,30,60)];
  const cleaned=refine(raw,evidence(Array.from({length:60},(_,i)=>60.4+i*.003)));
  const exported=buildExports(createMelodyDocument({id:'acoustic-test',durationSec:.8},{},raw,cleaned,0));
  const event=hummingEvent(exported.document,{sessionId:'test',eventId:'acoustic-event'});
  assert.deepEqual(event.payload.melody.notes.map(n=>[n.pitchMidi,n.startSec,n.durationSec]),[[60,0,.6]]);
  assert.deepEqual(exported.playbackNotes.map(n=>[n.midi,n.time,n.duration]),[[60,0,.6]]);
  assert.deepEqual(event.payload.melody.results.raw.notes,raw);
  assert.equal(event.payload.melody.notes[0].modelActivation,null);
  assert.equal(event.confidence,null);
});
