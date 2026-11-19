import {SAMPLE_RATE as rate} from './audio-pipeline.js';
import {quantile} from './humming-acoustics.js';
import {estimateTempo} from './humming-tempo.js';
import {independentPitch, PITCH_HOP} from './humming-pyin.js';

const floorMs=80, round=(value,places)=>Number(value.toFixed(places));
const tick=i=>i*PITCH_HOP/rate;

// Compute the benchmark's Gaussian RMS only at the points used for short notes.
function envelope(samples) {
  const sd=.007*rate, radius=Math.round(4*sd);
  const kernel=Float64Array.from({length:2*radius+1},(_,j)=>Math.exp(-.5*((j-radius)/sd)**2));
  const norm=kernel.reduce((a,b)=>a+b,0), cache=new Map();
  function at(i) {
    if (!cache.has(i)) {
      let power=0;
      for(let k=-radius;k<=radius;k++) {
        const j=i*PITCH_HOP+k;
        const pos=j<0?-j-1:j>=samples.length?2*samples.length-1-j:j;
        power+=kernel[k+radius]*(samples[pos]??0)**2;
      }
      cache.set(i,Math.sqrt(power/norm));
    }
    return cache.get(i);
  }
  return (start,end,q)=>{
    const values=[];
    for(let i=Math.max(0,Math.floor(start*rate/PITCH_HOP));i*PITCH_HOP<samples.length && tick(i)<end;i++) {
      if(tick(i)>=start) values.push(at(i));
    }
    return values.length?quantile(values,q):null;
  };
}

export function filterShortHummingNotes(samples,base,raw) {
  const tempo=estimateTempo(samples), energy=envelope(samples);
  let pitches;
  function hasIndependentAttack(n,i) {
    const a=n.startSec,b=a+n.durationSec,prev=base.notes[i-1],next=base.notes[i+1];
    const left=a-(prev?prev.startSec+prev.durationSec:0);
    const right=(next?next.startSec:samples.length/rate)-b;
    if (round(left,5)<.025 || round(right,5)<.025) return false;
    if (!raw.some(r=>r.pitchMidi===n.pitchMidi &&
      Math.min(b,r.startSec+r.durationSec)-Math.max(a,r.startSec)>=Math.min(.035,.45*n.durationSec))) return false;
    const before=energy(a-.050,a-.005,.25);
    const body=energy(a+.18*n.durationSec,b-.12*n.durationSec,.65)??0;
    if (before===null || round(body/Math.max(.0003,before),3)<2) return false;
    pitches??=independentPitch(samples);
    let frames=0, supported=0, streak=0, longest=0;
    for(let j=Math.max(0,Math.floor(a*rate/PITCH_HOP));j<pitches.length && tick(j)<b;j++) {
      if(tick(j)<a) continue;
      frames++;
      if(Math.abs(pitches[j]-n.pitchMidi)<=.5) {supported++;streak++;longest=Math.max(streak,longest);}
      else streak=0;
    }
    return frames>0 && round(supported/frames,4)>=.60 && round(tick(longest)*1000,2)>=45;
  }
  const edits=[...base.edits];
  const notes=base.notes.filter((n,i)=>{
    const ms=n.durationSec*1000+1e-8;
    if (ms>=tempo.thresholdMs || (ms>=floorMs && hasIndependentAttack(n,i))) return true;
    edits.push({type:'drop_short_output',pitchMidi:n.pitchMidi,startSec:n.startSec,durationSec:n.durationSec,
      thresholdMs:tempo.thresholdMs});
    return false;
  });
  return {...base,notes,edits,policy:{...base.policy,durationGate:{experiment:'strict',normalMs:tempo.thresholdMs,
    estimatedBpm:tempo.bpm,candidateBpm:tempo.candidate,shortEvidenceFloorMs:floorMs}}};
}
