import { decodeHumming, frameRms } from './continuity.js';
import { trackHummingPitch } from './pitch-track.js';
import { SAMPLE_RATE } from './audio-pipeline.js';
import { stationaryComponent, fineEnvelope, hzFor, end, roundTime } from './humming-acoustics.js';

export const SEGMENT_POLICY = Object.freeze({drone:true,voicing:.15,minimum:.05,shortEvidence:true,refine:false,snap:true,releaseWindow:.12});

function acousticEdges(fine) {
  const {rms,hop}=fine, sorted=rms.slice().sort((a,b)=>a-b);
  const floor=sorted[Math.floor((sorted.length-1)*.12)], loud=sorted[Math.floor((sorted.length-1)*.94)];
  const gate=Math.max(.00035,floor*1.65,floor+.075*(loud-floor));
  const starts=[],ends=[];
  for(let i=2;i<rms.length-2;i++){
    const up=rms[i]>=gate&&rms[i+1]>=gate, down=rms[i]<gate&&rms[i+1]<gate;
    if(up&&rms[i-2]<gate&&rms[i-1]<gate) starts.push(i*hop);
    if(down&&rms[i-2]>=gate&&rms[i-1]>=gate) ends.push(i*hop);
  }
  return {starts,ends};
}

function maskedFrames(trace,pcm,tones){
  if(!tones.length) return trace.frames;
  return trace.frames.map((row,i)=>{
    const copy=row.slice(),center=Math.min(pcm.length-1,Math.round(trace.times[i]*SAMPLE_RATE));
    for(const tone of tones){
      const hz=hzFor(tone.midi), half=Math.round(SAMPLE_RATE*Math.max(.035,3/hz)/2);
      const a=Math.max(0,center-half),b=Math.min(pcm.length,center+half);
      let re=0,im=0;
      for(let k=a;k<b;k+=3){
        const angle=2*Math.PI*hz*k/SAMPLE_RATE;
        re+=pcm[k]*Math.cos(angle);im+=pcm[k]*Math.sin(angle);
      }
      const bin=Math.round(tone.midi)-21;
      if(bin>=0 && bin<copy.length && 6*Math.hypot(re,im)/Math.max(1,b-a)/tone.amplitude<.85) copy[bin]*=.025;
    }
    return copy;
  });
}

function nearest(points,time,limit){
  let best=time;
  for(const point of points){
    const distance=Math.abs(point-time);
    if(distance<limit){best=point;limit=distance;}
  }
  return best;
}

export function voicedSegments(samples,trace) {
  const {times,onsets}=trace, reference=stationaryComponent(samples);
  const edges=acousticEdges(fineEnvelope(reference.pcm));
  const pcm=reference.tones.length?Float32Array.from(reference.pcm):samples;
  const frames=maskedFrames(trace,pcm,reference.tones);
  const rms=frameRms(pcm,SAMPLE_RATE,times);
  const {pitches}=trackHummingPitch(pcm,SAMPLE_RATE,times,frames);
  const durationSec=samples.length/SAMPLE_RATE;
  const {notes,edits}=decodeHumming(frames,onsets,times,{durationSec,rms,policy:{minimumNoteSec:SEGMENT_POLICY.minimum}});
  const support=notes.map(note=>{
    const [a,b]=note.frameRange;
    const found=pitches.slice(a,b).filter(v=>Number.isFinite(v)&&Math.abs(v-note.pitchMidi)<1.1);
    return found.length/Math.max(1,b-a);
  });
  const kept=[];
  if(support.some(value=>value>=SEGMENT_POLICY.voicing)){
    for(const [i,note] of notes.entries()){
      if(note.durationSec<.07 && !(note.onsetActivation>=.60 && support[i]>=.30 &&
        edges.starts.some(t=>Math.abs(t-note.startSec)<=.039))) continue;
      let a=note.startSec,b=end(note);
      const start=nearest(edges.starts,a,.068);
      const stop=nearest(edges.ends.filter(t=>t>start+.025),b,SEGMENT_POLICY.releaseWindow);
      if(stop-start>=.025){a=start;b=stop;}
      a=Math.max(0,a); b=Math.min(durationSec,b);
      kept.push({...note,startSec:roundTime(a),durationSec:roundTime(b-a)});
    }
  }
  for(let i=0;i<kept.length-1;i++){
    if(end(kept[i])>kept[i+1].startSec) kept[i].durationSec=roundTime(kept[i+1].startSec-kept[i].startSec);
  }
  return {notes:kept.filter(n=>n.durationSec>0),pitches,edits};
}
