import test from 'node:test';
import assert from 'node:assert/strict';
import midiPackage from '@tonejs/midi';
import { SAMPLE_RATE, prepareSamples, measureAudio } from '../src/audio-pipeline.js';
import { standardizeNotes, cleanNotes, createMelodyDocument, buildExports, TIMBRES } from '../src/melody.js';
const { Midi } = midiPackage;

function fixture() {
  const samples = new Float32Array(SAMPLE_RATE*3);
  for (const [start,end] of [[.6,1.1],[1.5,2.2]]) {
    for (let i=Math.round(start*SAMPLE_RATE);i<Math.round(end*SAMPLE_RATE);i++) samples[i]=.2*Math.sin(2*Math.PI*220*i/SAMPLE_RATE);
  }
  return samples;
}
const note = (start,duration,pitch=60,onset=.8) => ({pitchMidi:pitch,startTimeSeconds:start,durationSeconds:duration,amplitude:.8,onsetActivation:onset});

test('optional trim preserves interior rests, original samples and original time mapping',()=>{
  const input=fixture(), before=input.slice();
  const result=prepareSamples(input,{normalize:false,trim:true});
  const p=result.metadata.processing;
  assert(p.trimStartSec>=.44 && p.trimStartSec<=.46);
  assert(p.trimEndSec>=.64 && p.trimEndSec<=.66);
  assert.equal(result.samples.length,Math.round((3-p.trimStartSec-p.trimEndSec)*SAMPLE_RATE));
  assert.deepEqual(input,before);
  assert.equal(result.samples[Math.round((1.3-p.trimStartSec)*SAMPLE_RATE)],0);
  const notes=standardizeNotes([note(.2,.4)],3,p.trimStartSec);
  assert(Math.abs(notes[0].startSec-(.2+p.trimStartSec))<1e-6);
});
test('untrimmed input retains its complete timeline; volume change cannot move notes',()=>{
  const input=fixture(), result=prepareSamples(input);
  assert.equal(result.samples.length,input.length);
  assert.equal(result.metadata.processing.trimStartSec,0);
  assert.equal(result.metadata.processing.trimEndSec,0);
  assert.equal(result.samples[0],0);
});
test('near silence is rejected without infinite gain or fabricated notes',()=>{
  const result=prepareSamples(new Float32Array(SAMPLE_RATE));
  assert.equal(result.metadata.quality.status,'rejected');
  assert.equal(result.metadata.processing.gain,1);
  assert(result.samples.every(Number.isFinite));
});
test('quiet audio receives bounded gain and a warning',()=>{
  const result=prepareSamples(Float32Array.from(fixture(),x=>x*.05));
  assert.equal(result.metadata.processing.gain,4);
  assert(result.metadata.quality.warnings.some(w=>w.code==='quiet'));
});
test('source clipping is reported even if stereo downmix hides it',()=>{
  const channel=Float32Array.from({length:1000},(_,i)=>i%2?1:-1);
  const result=prepareSamples(fixture(),{sourceMetrics:measureAudio([channel])});
  assert(result.metadata.quality.warnings.some(w=>w.code==='possible_clipping'));
  assert.throws(()=>measureAudio([new Float32Array([NaN])]),/无效采样/);
});
test('validity bounds clip note ends and reject malformed pitches and durations',()=>{
  const result=standardizeNotes([note(-.05,.2),note(2.8,.5),note(1,-.1),note(NaN,.2),note(1,.2,60.5)],3);
  assert.equal(result.length,2);
  assert.equal(result[0].startSec,0); assert.equal(result[0].durationSec,.15);
  assert.equal(result[1].durationSec,.2);
});
test('weak short tails merge without altering raw notes; strong repeats and uncertain onsets survive',()=>{
  const raw=standardizeNotes([note(.3,.5),note(.8,.1,60,.05),note(1,.1,60,.9),note(1.1,.1,60,null),note(1.2,.3,60,.01)],3);
  const before=structuredClone(raw), cleaned=cleanNotes(raw);
  assert.equal(cleaned.edits.length,1); assert.equal(cleaned.notes.length,4);
  assert.equal(cleaned.notes[0].durationSec,.6);
  assert.deepEqual(cleaned.notes[0].sourceNoteIds,['n1','n2']);
  assert.deepEqual(raw,before);
});
test('suspected octaves and other simultaneous pitches are retained for review',()=>{
  const raw=standardizeNotes([note(.3,.5,60),note(.3,.5,72),note(.3,.5,64)],3);
  assert.equal(cleanNotes(raw).notes.length,3);
});
test('both variants and all timbres export consistent MIDI/JSON; free rhythm and trailing silence survive',()=>{
  const raw=standardizeNotes([note(.317,.502),note(.82,.1,60,.05),note(1.321,.412,64)],3);
  const doc=createMelodyDocument({id:'test',durationSec:3},prepareSamples(fixture()).metadata,raw,cleanNotes(raw),100);
  for (const variant of ['raw','cleaned']) for (const [timbre,definition] of Object.entries(TIMBRES)) {
    const result=buildExports(doc,variant,timbre), midi=new Midi(result.bytes);
    const actual=midi.tracks[0].notes, expected=result.document.notes;
    assert.equal(actual.length,expected.length);
    assert.equal(result.document.selectedResult,variant);
    assert.equal(midi.tracks[0].instrument.number,definition.program);
    assert.equal(result.document.playback.gmProgramNumber,definition.program);
    assert.equal(result.document.tempo.bpm,null);
    assert.equal(result.document.tempo.quantized,false);
    const tolerance=result.document.midiExport.timeRoundingToleranceSec+1e-9;
    expected.forEach((n,index)=>{
      const parsed=actual[index]; assert.equal(parsed.midi,n.pitchMidi);
      assert(Math.abs(parsed.time-n.startSec)<=tolerance);
      assert(Math.abs(parsed.time+parsed.duration-n.startSec-n.durationSec)<=tolerance);
      assert(Math.abs(parsed.velocity-n.velocity01)<=1/127);
    });
    assert.equal(midi.header.meta.find(event=>event.text==='JamMate original audio end').ticks,midi.header.secondsToTicks(3));
    assert(Math.abs(actual[0].time-.317)<tolerance);
    assert.deepEqual(actual.map(n=>n.toJSON()),result.playbackNotes.map(n=>n.toJSON()));
  }
});
test('empty detection has an explicit no_notes state',()=>{
  const doc=createMelodyDocument({id:'empty',durationSec:3},prepareSamples(fixture()).metadata,[],cleanNotes([]),100);
  assert.equal(doc.status,'no_notes');
  assert.equal(buildExports(doc).document.notes.length,0);
});
