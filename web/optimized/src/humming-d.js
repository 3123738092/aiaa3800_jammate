import { SAMPLE_RATE } from './audio-pipeline.js';
import { SEGMENT_POLICY, voicedSegments } from './humming-segments.js';
import { end, hzFor, interval, lower, median, pitchBands, quantile, roundTime, smoothEnvelope, steadyCarrier } from './humming-acoustics.js';

const PREVIOUS_POLICY = Object.freeze({...SEGMENT_POLICY, variant:'D',
  samePitchReattack:{method:'smooth_breath_residual', dip:.14, onset:.30},
  extended:['localBackground','samePitchReentry','alternativeCandidate','continuousGlide','movingTone']});
const POLICY = Object.freeze({...PREVIOUS_POLICY,
  samePitchReattack:{...PREVIOUS_POLICY.samePitchReattack,checkVoicedBend:true},
  extended:[...PREVIOUS_POLICY.extended,'semitoneLeadIn']});

function bounded(note,a,b,times) {
  const first=Math.min(times.length-2,lower(times,a));
  const stop=Math.max(first+1,Math.min(times.length-1,lower(times,b)));
  return {...note,startSec:roundTime(a),durationSec:roundTime(b-a),frameRange:[first,stop]};
}

// Intercept of a quadratic fitted to symmetric shoulders around each possible attack.
function breathPrediction(values,step,inner,outer){
  const positions=[];
  let sum2=0,sum4=0;
  for(let j=-Math.ceil(outer/step);j<=Math.ceil(outer/step);j++){
    const distance=Math.abs(j*step);
    if(distance<inner||distance>outer) continue;
    const square=(distance/outer)**2;
    positions.push({j,square});sum2+=square;sum4+=square**2;
  }
  const denom=positions.length*sum4-sum2**2;
  const weights=positions.map(({j,square})=>[j,(sum4-sum2*square)/denom]);
  return values.map((_,i)=>weights.reduce((a,[j,w])=>a+w*(values[i+j]??0),0));
}

// An uninterrupted, bending vowel can make both the envelope and the model's
// onset score pulse. Only veto the cut if the model still supports that pitch
// throughout the dip and the acoustic pitch turns back within the same window.
function bendsThroughDip(pitches, trace, pitch, time) {
  const [a,b] = interval(trace.times, time - .045, time + .045);
  const track = pitches.slice(a,b);
  if (track.length < 6 || !track.every(Number.isFinite)) return false;
  const levels = trace.frames.slice(a,b).map(row => row[pitch - 21]);
  const shoulders = Math.min(levels[0], levels.at(-1));
  if (Math.min(...levels) < Math.max(.30, .70 * shoulders)) return false;
  // Adjacent-frame means prevent a single errant F0 estimate deciding the cut.
  const pairs = track.slice(1).map((value,i) => (value + track[i]) / 2);
  const low = Math.min(...pairs), high = Math.max(...pairs);
  const first = pairs[0], last = pairs.at(-1);
  const reversal = Math.max(high - Math.max(first,last), Math.min(first,last) - low);
  return high - low >= .75 && reversal >= .35;
}

function rearticulate(notes,x,trace,pitches,edits,rule){
  if(!notes.some(n=>n.durationSec>.210)) return notes;
  const {values,times,step}=smoothEnvelope(x), ft=trace.times.slice(0,-1);
  const short=breathPrediction(values,step,.032,.060), long=breathPrediction(values,step,.050,.090);
  const deficit=values.map((v,i)=>Math.min(short[i]-v,long[i]-v)/Math.max(short[i],long[i],.001));
  const maxima=[];
  for(let i=1;i<deficit.length-1;i++){
    if(deficit[i]>=.08&&deficit[i]>deficit[i-1]&&deficit[i]>=deficit[i+1]) maxima.push(i);
  }
  const peaks=[], distance=Math.max(1,Math.round(.085/step));
  for(const i of maxima.sort((a,b)=>deficit[b]-deficit[a]||b-a)){
    if(peaks.every(j=>Math.abs(i-j)>=distance)) peaks.push(i);
  }
  return notes.flatMap(note=>{
    const cuts=[], pitch=note.pitchMidi;
    for(const i of peaks){
      const mid=times[i],score=deficit[i];
      if(mid<=note.startSec+.105 || mid>=end(note)-.105 || score<rule.dip) continue;
      const shoulder=(a,b)=>median(values.slice(lower(times,mid+a,true),lower(times,mid+b)));
      const level=Math.min(shoulder(-.055,-.026),shoulder(.026,.055)), ratio=values[i]/level;
      if(level<.0012||!(ratio>.065&&ratio<.925)) continue;
      const supported=(a,b)=>{
        const side=pitches.slice(lower(ft,mid+a,true),lower(ft,mid+b));
        return side.length>0 && side.filter(p=>Number.isFinite(p)&&Math.abs(p-pitch)<1.1).length>=side.length*.5;
      };
      if(!supported(-.067,-.018)||!supported(.018,.067)) continue;
      const nearby=trace.onsets.slice(lower(ft,mid-.042,true),lower(ft,mid+.042));
      if(!nearby.length) continue;
      const onset=Math.max(...nearby.map(row=>row[pitch-21]));
      if(onset<rule.onset && score<rule.dip+.16) continue;
      if(rule.checkVoicedBend && bendsThroughDip(pitches,trace,pitch,mid)) continue;
      if(cuts.every(t=>Math.abs(t-mid)>.13)) cuts.push(mid);
    }
    if(!cuts.length) return [note];
    const edges=[note.startSec,...cuts.sort((a,b)=>a-b),end(note)];
    edits.push({type:'split_same_pitch_reattack',pitchMidi:pitch,cutTimes:cuts});
    return edges.slice(1).map((b,j)=>bounded(note,edges[j],b,trace.times));
  });
}

function nearbyHigher(note,notes,limit){
  return notes.filter(other=>other.pitchMidi>=note.pitchMidi+4 && other.durationSec>=.18 &&
    Math.max(note.startSec-end(other),other.startSec-end(note))<=limit);
}

function coherentBed(note,anchors,carrier){
  const {times,levels,phase}=carrier;
  const [a,b]=interval(times,note.startSec+.02,end(note)-.02);
  if(b-a<2) return false;
  for(const anchor of anchors){
    const [l,r]=interval(times,anchor.startSec+.048,end(anchor)-.048);
    if(r-l<4) continue;
    const level=median(levels.slice(l,r)), ratio=median(levels.slice(a,b))/level;
    if(level<.004||ratio<.65||ratio>1.48) continue;
    const lo=Math.min(l,a),hi=Math.max(r,b);
    if(quantile(levels.slice(lo,hi),.12)<.55*level) continue;
    const center=(times[lo]+times[hi-1])/2;
    let numerator=0,denominator=0;
    for(let i=lo;i<hi;i++){const t=times[i]-center;numerator+=t*phase[i];denominator+=t*t;}
    const slope=numerator/denominator;
    if(Math.abs(slope)/(2*Math.PI)>=.022*hzFor(note.pitchMidi)) continue;
    let re=0,im=0;
    for(let i=lo;i<hi;i++){const angle=phase[i]-slope*times[i];re+=Math.cos(angle);im+=Math.sin(angle);}
    if(Math.hypot(re,im)/(hi-lo)>=.93) return true;
  }
  return false;
}

function spansAbove(times,values,a,b,threshold){
  const [lo,hi]=interval(times,a,b),spans=[];
  let i=lo;
  while(i<hi){
    if(values[i]<=threshold){i++;continue;}
    const first=i++;
    while(i<hi&&values[i]>threshold)i++;
    spans.push([first,i]);
  }
  return spans;
}

// A sustained low component must cross a nearby higher note. A local rise can retain
// an intentional vocal note at exactly the pitch of that background.
function backgroundReplacement(note,anchors,spectrum,already,ft){
  const {times,band}=spectrum,p=note.pitchMidi,amp=band(p),a=note.startSec,b=end(note);
  const [l,r]=interval(times,a+.025,b-.025);
  if(r-l<2) return null;
  for(const anchor of anchors){
    const [i,j]=interval(times,anchor.startSec+.052,end(anchor)-.052);
    if(j-i<5) continue;
    const floor=median(amp.slice(i,j)),ratio=quantile(amp.slice(l,r),.35)/floor;
    if(floor<.0032||!(ratio>.48&&ratio<1.62)||
      quantile(amp.slice(Math.min(l,i),Math.max(r,j)),.12)<=.46*floor) continue;
    if(!already && median(band(anchor.pitchMidi).slice(i,j))<1.25*floor) continue;
    return spansAbove(times,amp,a-.025,b+.03,Math.max(.009,1.72*floor)).flatMap(([first,last])=>{
      const start=Math.max(a,times[first]), stop=Math.min(b,times[last-1]+.004);
      return times[last-1]-times[first]>=.075 && stop-start>=.065 ? [bounded(note,start,stop,ft)] : [];
    });
  }
  return already?[]:null;
}

function recoverCandidates(raw,vacated,kept,spectrum,ft,duration){
  const {times,band}=spectrum,recovered=[];
  const overlaps=(a,b,tolerance)=>[...kept,...recovered].some(k=>Math.min(end(k),b)-Math.max(k.startSec,a)>tolerance);
  for(const n of [...raw].sort((a,b)=>(b.modelActivation??0)-(a.modelActivation??0))){
    const a=n.startSec,b=end(n),p=n.pitchMidi;
    if(n.durationSec<.075||!vacated.some(v=>p>=v.pitchMidi+3&&Math.min(end(v),b)-Math.max(v.startSec,a)>.65*n.durationSec)||
      overlaps(a,b,.028)) continue;
    const amp=band(p),[l,r]=interval(times,a+.018,b-.018);
    const around=[...amp.slice(lower(times,a-.13),lower(times,a-.036)),...amp.slice(lower(times,b+.036,true),lower(times,b+.13,true))];
    if(r-l<3||!around.length) continue;
    const level=median(amp.slice(l,r)),floor=quantile(around,.32);
    if(level<Math.max(.010,2.6*floor)) continue;
    const chunks=spansAbove(times,amp,a-.045,b+.045,Math.max(1.7*floor,level*.18))
      .filter(([i,j])=>times[j-1]>a+.025&&times[i]<b-.025).sort((x,y)=>(y[1]-y[0])-(x[1]-x[0]));
    if(!chunks.length) continue;
    const [i,j]=chunks[0], start=times[i],stop=Math.min(duration,times[j-1]+.004);
    if(stop-start>=.06&&!overlaps(start,stop,.015)) recovered.push(bounded(n,start,stop,ft));
  }
  return recovered;
}

function mergeGlides(notes,pitches,ft,edits){
  const out=[];
  for(let i=0;i<notes.length;){
    let j=i+1;
    const direction=j<notes.length?Math.sign(notes[j].pitchMidi-notes[i].pitchMidi):0;
    while(direction&&j<notes.length){
      const delta=(notes[j].pitchMidi-notes[j-1].pitchMidi)*direction;
      if(delta<=0||delta>4||Math.abs(notes[j].startSec-end(notes[j-1]))>=.048) break;
      j++;
    }
    const continuous=n=>{
      const track=pitches.slice(lower(ft,n.startSec-.012),lower(ft,end(n)+.012)).filter(Number.isFinite);
      if(track.length<5||n.durationSec>.37) return false;
      const smooth=track.map((x,k)=>median([track[Math.max(0,k-1)],x,track[Math.min(track.length-1,k+1)]]));
      const reverse=smooth.slice(1).filter((x,k)=>(x-smooth[k])*direction<-.10).length;
      return direction*(median(smooth.slice(-2))-median(smooth.slice(0,2)))>=.46 && reverse<=.25*(smooth.length-1);
    };
    if(j-i>=3 && notes[i].durationSec>=.19 && notes[j-1].durationSec>=.19 && notes.slice(i+1,j-1).every(continuous)){
      const first=notes[i],last=notes[j-1],mid=(end(first)+last.startSec)/2;
      out.push(bounded(first,first.startSec,mid,ft),bounded(last,mid,end(last),ft));
      edits.push({type:'merge_continuous_glide',startSec:first.startSec,endSec:end(last)});
      i=j;
    } else {out.push(notes[i]);i++;}
  }
  return out;
}

// A legato semitone approach crosses the MIDI boundary gradually. A quick
// intentional step has a plateau or an abrupt jump at the boundary instead.
function isSemitoneLeadIn(lead, target, pitches, times) {
  if (Math.abs(target.pitchMidi - lead.pitchMidi) !== 1 || lead.durationSec > .12 ||
      target.durationSec < .16 || Math.abs(target.startSec - end(lead)) > .012) return false;
  const boundary = target.startSec;
  const [a,b] = interval(times, Math.max(lead.startSec, boundary - .10), boundary + .050);
  let first = a;
  while (first < b && !Number.isFinite(pitches[first])) first++;
  const track = pitches.slice(first,b);
  if (track.length < 6 || times[first] > boundary - .032 || !track.every(Number.isFinite)) return false;
  const dir = Math.sign(target.pitchMidi - lead.pitchMidi);
  const progress = track.map(p => (p - lead.pitchMidi) * dir);
  const crossing = lower(times,boundary) - first;
  const [l,r] = interval(times, boundary + .040, boundary + .085);
  const arrival = pitches.slice(l,r).filter(Number.isFinite);
  if (arrival.length < 3 || Math.abs(median(arrival) - target.pitchMidi) > .45) return false;
  if (crossing < 2 || crossing + 2 >= progress.length ||
      progress[0] > .20 || progress.at(-1) < .70 ||
      progress[crossing] < .15 || progress[crossing] > .85) return false;
  let nearLeadSince = null;
  for (let i = 0; i < progress.length; i++) {
    // Protect a short held pitch, including one followed by a quick slide.
    if (Math.abs(progress[i]) <= .22) {
      nearLeadSince ??= times[first+i];
      if (times[first+i] - nearLeadSince >= .035) return false;
    } else nearLeadSince = null;
    if (!i) continue;
    const step = progress[i] - progress[i-1];
    if (step < -.12 || (step > .50 && progress[i] > .20)) return false;
  }
  return true;
}

function applyDRules(samples,trace,raw,base,policy){
  const {pitches}=base, edits=[...base.edits], ft=trace.times;
  const articulated=policy.samePitchReattack
    ? rearticulate(base.notes,samples,trace,pitches,edits,policy.samePitchReattack)
    : base.notes;
  const carriers=new Map(), kept=[], vacated=[];
  let spectrum;
  for(const n of articulated){
    const anchors=nearbyHigher(n,articulated,.70);
    if(!anchors.length){kept.push(n);continue;}
    spectrum??=pitchBands(samples,[...articulated,...raw].map(k=>k.pitchMidi));
    const near=nearbyHigher(n,anchors,.65);
    if(near.length&&!carriers.has(n.pitchMidi)) carriers.set(n.pitchMidi,steadyCarrier(samples,n.pitchMidi));
    const veto=near.length>0 && coherentBed(n,near,carriers.get(n.pitchMidi));
    const replacement=backgroundReplacement(n,anchors,spectrum,veto,ft);
    if(replacement===null) kept.push(n);
    else{
      kept.push(...replacement);vacated.push(n);
      edits.push({type:'filter_local_background',pitchMidi:n.pitchMidi,startSec:n.startSec,endSec:end(n)});
    }
  }
  if(vacated.length) kept.push(...recoverCandidates(raw,vacated,kept,spectrum,ft,samples.length/SAMPLE_RATE));
  kept.sort((a,b)=>a.startSec-b.startSec||a.pitchMidi-b.pitchMidi);
  for(let i=0;i<kept.length-1;i++){
    if(end(kept[i])>kept[i+1].startSec) kept[i]=bounded(kept[i],kept[i].startSec,kept[i+1].startSec,ft);
  }
  const glides=mergeGlides(kept.filter(n=>n.durationSec>0),pitches,ft,edits);
  const final=[];
  for (const note of glides) {
    const lead = final.at(-1);
    if (lead && policy.extended.includes('semitoneLeadIn') && isSemitoneLeadIn(lead,note,pitches,ft)) {
      final[final.length-1] = bounded(note,lead.startSec,end(note),ft);
      edits.push({type:'absorb_semitone_lead_in',pitchMidi:lead.pitchMidi,targetPitchMidi:note.pitchMidi,
        startSec:lead.startSec,boundarySec:note.startSec});
    } else final.push(note);
  }
  const notes=final.map((n,i)=>{
    const sources=raw.filter(r=>r.startSec<end(n)&&n.startSec<end(r));
    const note={...n,id:'d'+(i+1),sourceNoteIds:sources.map(r=>r.id)};
    if(!base.notes.some(b=>b.pitchMidi===n.pitchMidi&&b.startSec===n.startSec&&b.durationSec===n.durationSec)){
      const [a,b]=n.frameRange,bin=n.pitchMidi-21,rows=trace.frames.slice(a,b);
      note.modelActivation=rows.length?rows.reduce((v,row)=>v+row[bin],0)/rows.length:null;
      note.onsetActivation=Math.max(...trace.onsets.slice(Math.max(0,a-1),a+2).map(row=>row[bin]));
    }
    return note;
  });
  return {notes,edits,policy,forceMonophonic:true,octaveCorrection:false};
}

export function decodeHummingRevisions(samples,trace,raw,comparePrevious=false,{legato=false}={}){
  const base=voicedSegments(samples,trace);
  const policy=legato ? {...POLICY,samePitchReattack:null} : POLICY;
  const cleaned=applyDRules(samples,trace,raw,base,policy);
  return comparePrevious ? {cleaned,previous:applyDRules(samples,trace,raw,base,legato?POLICY:PREVIOUS_POLICY)} : {cleaned};
}

export function decodeDefaultHumming(samples,trace,raw){
  return decodeHummingRevisions(samples,trace,raw).cleaned;
}
