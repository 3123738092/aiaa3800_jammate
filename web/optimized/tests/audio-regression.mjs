// Offline real Basic Pitch inference; no browser mocks or network needed.
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
import * as tf from '@tensorflow/tfjs';
import {BasicPitch} from '@spotify/basic-pitch';
import {prepareSamples} from '../src/audio-pipeline.js';
import {transcribeHummingFrames} from '../src/transcribe-humming.js';
const root=path.resolve(import.meta.dirname,'../../..'),out=path.join(root,'tmp/humming-real');
fs.mkdirSync(out,{recursive:true});
await tf.setBackend('cpu');await tf.ready();
const packageEntry=createRequire(import.meta.url).resolve('@spotify/basic-pitch');
const modelRoot=path.resolve(path.dirname(packageEntry),'../model');
const model=JSON.parse(fs.readFileSync(path.join(modelRoot,'model.json')));
const bytes=Buffer.concat(model.weightsManifest.flatMap(g=>g.paths.map(p=>fs.readFileSync(path.join(modelRoot,p)))));
const graph=await tf.loadGraphModel({load:async()=>({modelTopology:model.modelTopology,
  weightSpecs:model.weightsManifest.flatMap(g=>g.weights),weightData:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),signature:model.signature})});
const bp=new BasicPitch(Promise.resolve(graph));
const expected=JSON.parse(fs.readFileSync(path.join(root,'web/fixtures/expected.json'))),report={};
for(const name of ['sustain','repeated','steps','rest','short','quiet','noisy']){
  const wav=fs.readFileSync(path.join(root,`web/fixtures/${name}.wav`));
  assert.equal(wav.readUInt32LE(24),22050);assert.equal(wav.readUInt16LE(22),1);assert.equal(wav.readUInt16LE(34),16);
  assert.equal(wav.toString('ascii',36,40),'data');
  const input=Float32Array.from({length:(wav.length-44)/2},(_,i)=>wav.readInt16LE(44+i*2)/32768);
  const prep=prepareSamples(input,{normalize:false}),frames=[],onsets=[];
  const start=performance.now();tf.engine().startScope();
  try{await bp.evaluateModel(prep.samples,(f,o)=>{frames.push(...f);onsets.push(...o);},()=>{});}finally{tf.engine().endScope();}
  const {cleaned:result}=transcribeHummingFrames(prep.samples,frames,onsets);
  const golden=expected[name].notes;
  const pitches=result.notes.map(n=>n.pitchMidi),correct=JSON.stringify(pitches)===JSON.stringify(golden.map(n=>n.pitch));
  const maxBoundaryErrorSec=correct?Math.max(...result.notes.flatMap((n,i)=>[Math.abs(n.startSec-golden[i].start),Math.abs(n.startSec+n.durationSec-golden[i].start-golden[i].duration)])):null;
  report[name]={pitches,expected:golden.map(n=>n.pitch),correct,maxBoundaryErrorSec,elapsedMs:Math.round(performance.now()-start)};
  console.log(name,JSON.stringify(report[name]));
}
fs.writeFileSync(path.join(out,'audio-regression.json'),JSON.stringify(report,null,2));
for(const [name,r]of Object.entries(report)){assert(r.correct,name+' pitches');assert(r.maxBoundaryErrorSec<.13,name+' boundaries');}
console.log('PASS: seven real-model audio regressions');
