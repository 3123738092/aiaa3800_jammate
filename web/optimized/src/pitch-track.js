// Acoustic evidence for single-voice humming. A YIN-style normalized difference
// estimates continuous pitch near the model's candidate; it does not infer a key.
export const PITCH_TRACK_POLICY=Object.freeze({version:'1.0.0',guideSemitones:4,
  maximumDifference:.5,minimumCoverage:.65,mergeDistanceSemitones:.55,
  stableRangeSemitones:.35,minimumStableSec:.09,minimumStableFraction:.7,
  maximumExcursionSec:.22,maximumExcursionSemitones:1.25,maximumGapSec:.035,
  attackRatio:2.2});
const median=values=>{const s=values.slice().sort((a,b)=>a-b),m=s.length>>1;return s.length?(s.length%2?s[m]:(s[m-1]+s[m])/2):null;};
const round=n=>Math.round(n*1e6)/1e6;

function summarizePitch(note,pitches,times){
  const start=note.frameRange?.[0]??0;
  const end=note.frameRange?.[1]??0;
  const values=pitches.slice(start,end).filter(Number.isFinite);
  let stable=0;
  for(let i=start;i<end;i++){
    let low=Infinity,high=-Infinity;
    for(let j=i;j<end;j++){
      const pitch=pitches[j];
      if(!Number.isFinite(pitch))break;
      low=Math.min(low,pitch);high=Math.max(high,pitch);
      if(high-low>PITCH_TRACK_POLICY.stableRangeSemitones)break;
      stable=Math.max(stable,times[j+1]-times[i]);
    }
  }
  return {center:median(values),coverage:values.length/Math.max(1,end-start),stableSec:stable};
}

export function trackHummingPitch(samples,sampleRate,times,frames){
  if(times.length!==frames.length+1||sampleRate<=0)throw Error('invalid pitch evidence dimensions');
  const factor=Math.max(1,Math.round(sampleRate/11025)),sr=sampleRate/factor;
  const audio=new Float32Array(Math.ceil(samples.length/factor));
  for(let i=0;i<audio.length;i++){let count=0;for(let j=0;j<factor&&i*factor+j<samples.length;j++){audio[i]+=samples[i*factor+j];count++;}audio[i]/=count;}
  const pitches=[],periodicity=[],cfg=PITCH_TRACK_POLICY;
  for(let i=0;i<frames.length;i++){
    let guide=36,peak=0;for(let p=36;p<=84;p++)if((frames[i][p-21]??0)>peak){guide=p;peak=frames[i][p-21];}
    pitches.push(null);periodicity.push(0);if(peak<.12)continue;
    const hz=p=>440*2**((p-69)/12),lo=sr/hz(guide+cfg.guideSemitones),hi=sr/hz(guide-cfg.guideSemitones);
    // Keep enough periods for bass voices, without smearing an 80 ms fast note
    // across a fixed 70 ms analysis window.
    const width=Math.round(Math.max(sr*.035,3*sr/hz(guide)));
    const maxlag=Math.ceil(hi)+2,start=Math.round(times[i]*sr)-Math.floor(width/2);
    const segment=new Float32Array(width+maxlag);let energy=0;
    for(let j=0;j<segment.length;j++){segment[j]=audio[start+j]??0;if(j<width)energy+=segment[j]**2;}
    if(Math.sqrt(energy/width)<.0005)continue;
    const normalized=new Float64Array(maxlag+1);normalized[0]=1;let cumulative=0;
    for(let lag=1;lag<=maxlag;lag++){
      let difference=0;for(let j=0;j<width;j++)difference+=(segment[j]-segment[j+lag])**2;
      cumulative+=difference;normalized[lag]=cumulative>1e-15?difference*lag/cumulative:1;
    }
    let best=-1;
    for(let lag=Math.max(2,Math.ceil(lo));lag<=Math.floor(hi);lag++)
      if(normalized[lag]<=normalized[lag-1]&&normalized[lag]<normalized[lag+1]&&(best<0||normalized[lag]<normalized[best]))best=lag;
    if(best<0||normalized[best]>=cfg.maximumDifference)continue;
    const denominator=normalized[best-1]-2*normalized[best]+normalized[best+1];
    const shift=denominator?Math.max(-.5,Math.min(.5,.5*(normalized[best-1]-normalized[best+1])/denominator)):0;
    pitches[i]=69+12*Math.log2(sr/(best+shift)/440);periodicity[i]=Math.max(0,1-normalized[best]);
  }
  return {pitches,periodicity,policy:{...cfg},meaning:'Model-guided acoustic periodicity, not calibrated confidence.'};
}

export function refineHummingWithPitch(decoded,{pitchTrack,times,rms}){
  const cfg=PITCH_TRACK_POLICY,pitches=pitchTrack.pitches;
  if(pitches.length+1!==times.length||rms.length!==pitches.length)throw Error('invalid acoustic refinement dimensions');
  const floor=Math.max(.0005,...rms.map(v=>v*.015));
  const supported=s=>s.center!==null&&s.coverage>=cfg.minimumCoverage;
  const attack=note=>{
    const a=note.frameRange[0];if(a<1)return true;
    const before=rms.slice(Math.max(0,a-4),a),after=rms.slice(a,a+4);
    return Math.max(...after)>=cfg.attackRatio*Math.max(.0001,Math.min(...before));
  };
  const touches=(left,right)=>{
    const gap=right.startSec-left.startSec-left.durationSec;
    if(gap<-.01||gap>cfg.maximumGapSec)return false;
    return rms.slice(Math.max(0,left.frameRange[1]-1),right.frameRange[0]+1).every(v=>v>floor);
  };
  const wrap=note=>{
    const summary=summarizePitch(note,pitches,times);
    return {note:structuredClone(note),stats:{...summary},minCenter:summary.center,maxCenter:summary.center};
  };
  const join=(left,right,middle=[])=>{
    const note={...left.note,durationSec:round(right.note.startSec+right.note.durationSec-left.note.startSec),
      sourceNoteIds:[...new Set([left.note,...middle,right.note].flatMap(n=>n.sourceNoteIds))],
      frameRange:[left.note.frameRange[0],right.note.frameRange[1]]};
    const s=summarizePitch(note,pitches,times);note.pitchMidi=Math.round(s.center);
    // A merged acoustic interpretation no longer has a single model-bin score.
    note.modelActivation=null;if(note.pitchMidi!==left.note.pitchMidi)note.onsetActivation=null;
    return {note,stats:s,minCenter:Math.min(left.minCenter,right.minCenter),maxCenter:Math.max(left.maxCenter,right.maxCenter)};
  };
  const items=[];
  for(const original of decoded.notes)items.push(wrap(original));
  const groups=[],edits=[...decoded.edits],removedNotes=[...(decoded.removedNotes??[])];
  for(let i=0;i<items.length;i++){
    const current=items[i],{note,stats:s}=current,previous=groups.at(-1),next=items[i+1];
    const unstable=supported(s)&&note.durationSec<=cfg.maximumExcursionSec&&s.stableSec<cfg.minimumStableSec&&s.stableSec/note.durationSec<cfg.minimumStableFraction;
    if(unstable&&previous&&next&&supported(previous.stats)&&supported(next.stats)&&
      touches(previous.note,note)&&touches(note,next.note)&&!attack(note)&&!attack(next.note)){
      const low=Math.min(previous.stats.center,next.stats.center),high=Math.max(previous.stats.center,next.stats.center);
      const distance=s.center<low?low-s.center:s.center>high?s.center-high:0;
      if(distance>.3&&distance<=cfg.maximumExcursionSemitones){
        if(high-low<=cfg.mergeDistanceSemitones){
          groups[groups.length-1]=join(previous,next,[note]);i++;
          edits.push({type:'absorb_unstable_pitch_excursion',sourceId:note.id,targetId:previous.note.id,evidence:s});
        }else{
          removedNotes.push(structuredClone(note));
          edits.push({type:'remove_unstable_pitch_excursion',sourceId:note.id,evidence:s});
        }
        continue;
      }
    }
    groups.push(current);
  }
  const merged=[];
  for(const group of groups){
    const previous=merged.at(-1);
    if(previous&&supported(previous.stats)&&supported(group.stats)&&
      Math.abs(previous.note.pitchMidi-group.note.pitchMidi)<=1&&touches(previous.note,group.note)&&!attack(group.note)&&
      Math.max(previous.maxCenter,group.maxCenter)-Math.min(previous.minCenter,group.minCenter)<=cfg.mergeDistanceSemitones){
      merged[merged.length-1]=join(previous,group);
      edits.push({type:'merge_pitch_boundary_drift',sourceId:group.note.id,targetId:previous.note.id,
        centers:[previous.stats.center,group.stats.center]});
    }else merged.push(group);
  }
  return {...decoded,notes:merged.map(g=>g.note),edits,removedNotes,
    pitchEvidence:items.map(({note,stats:s})=>({noteId:note.id,...s})),
    policy:{...decoded.policy,acousticRefinement:{...cfg}}};
}
