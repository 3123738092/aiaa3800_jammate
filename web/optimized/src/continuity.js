// Single-voice humming decoder. Raw polyphonic Basic Pitch output is retained.
// Times are provided by the installed model's noteFramesToTime implementation.
export const CONTINUITY = Object.freeze({version:'1.1.1', activation:.18, holdActivation:.12,
  pitchMin:36,pitchMax:84,holdRatio:.8,changeSec:.14,articulatedChangeSec:.035,
  minimumNoteSec:.07,bridgeGapSec:.09,strongOnset:.65,repeatSeparationSec:.14});
const round=n=>Math.round(n*1e6)/1e6;

export function decodeHumming(frames,onsets,times,{durationSec,offsetSec=0,rms=null,raw=[],policy={}}){
  const cfg={...CONTINUITY,...policy};
  if(frames.length!==onsets.length||times.length!==frames.length+1)throw Error('frame/time dimensions do not match');
  if(!Number.isFinite(durationSec)||durationSec<=0||!Number.isFinite(offsetSec)||offsetSec<0)throw Error('invalid recording bounds');
  if(times.some((t,i)=>!Number.isFinite(t)||(i&&t<=times[i-1])))throw Error('frame times must increase');
  const notes=[],edits=[];let current=null,pending=null,gap=null,lastPitch=null;
  const energyFloor=rms?Math.max(.0005,Math.max(...rms)*.015):0;
  const emit=end=>{
    if(!current)return;
    const startSec=Math.max(offsetSec,offsetSec+times[current.start]);
    const endSec=Math.min(durationSec,offsetSec+times[end]);
    if(endSec-startSec>=cfg.minimumNoteSec){
      const sourceNoteIds=raw.filter(n=>n.startSec<endSec&&n.startSec+n.durationSec>startSec).map(n=>n.id);
      notes.push({id:`h${notes.length+1}`,pitchMidi:current.pitch,startSec:round(startSec),durationSec:round(endSec-startSec),
        velocity01:90/127,modelActivation:current.sum/current.count,onsetActivation:onsets[current.start]?.[current.pitch-21]??null,
        sourceNoteIds,frameRange:[current.start,end]});
    }
    current=null;pending=null;
  };
  const start=(pitch,i)=>{current={pitch,start:i,sum:frames[i][pitch-21],count:1};pending=null;gap=null;};
  for(let i=0;i<frames.length;i++){
    const row=frames[i];let pitch=cfg.pitchMin,peak=-Infinity;
    for(let p=cfg.pitchMin;p<=cfg.pitchMax;p++)if((row[p-21]??0)>peak){peak=row[p-21];pitch=p;}
    const previous=current?.pitch??lastPitch;
    // A previous note's model tail can remain almost as strong as a newly
    // articulated short note. Let that candidate pass persistence checks;
    // otherwise hysteresis can erase the whole new note before it is tested.
    const candidateAttack=(onsets[i]?.[pitch-21]??0)>=cfg.strongOnset ||
      (pending?.pitch===pitch&&(onsets[pending.start]?.[pitch-21]??0)>=cfg.strongOnset);
    if(!candidateAttack&&previous!==null && row[previous-21]>=cfg.holdActivation && row[previous-21]>=peak*cfg.holdRatio)pitch=previous;
    const audible=!rms||rms[i]>energyFloor;
    const threshold = current?.pitch === pitch ? cfg.holdActivation : cfg.activation;
    if((row[pitch-21]??0)<threshold||!audible){
      if(current){gap??={start:i,silent:0};if(!audible)gap.silent+=times[i+1]-times[i];
        if(times[i+1]-times[gap.start]>cfg.bridgeGapSec||gap.silent>=.03){emit(gap.start);gap=null;lastPitch=null;}}
      continue;
    }
    if(current&&gap){
      if(pitch!==current.pitch){emit(gap.start);}else edits.push({type:'bridge_detection_gap',startFrame:gap.start,endFrame:i});
      gap=null;
    }
    if(!current){start(pitch,i);lastPitch=pitch;continue;}
    if(pitch!==current.pitch){
      if(!pending||pending.pitch!==pitch)pending={pitch,start:i};
      const attack=onsets[pending.start]?.[pitch-21]??0;
      const required=attack>=cfg.strongOnset?cfg.articulatedChangeSec:cfg.changeSec;
      if(times[i+1]-times[pending.start]>=required){const at=pending.start;emit(at);start(pitch,at);}
    }else{
      if(pending)edits.push({type:'hold_pitch_glitch',startFrame:pending.start,endFrame:i});
      pending=null;
      // An onset must coincide with an acoustic re-attack; activation peaks alone
      // commonly split sustained humming. True rests were handled above.
      const onset=onsets[i]?.[pitch-21]??0;
      const localPeak=onset>(onsets[i-1]?.[pitch-21]??0)&&onset>=(onsets[i+1]?.[pitch-21]??0);
      const energyAttack=rms&&i>=3&&rms[i]>3*Math.max(.0001,Math.min(...rms.slice(i-3,i)));
      if(onset>=cfg.strongOnset&&localPeak&&energyAttack&&times[i]-times[current.start]>=cfg.repeatSeparationSec){emit(i);start(pitch,i);}
    }
    if(current){current.sum+=row[current.pitch-21]??0;current.count++;lastPitch=current.pitch;}
  }
  emit(gap?.start??frames.length);
  return {notes,edits,policy:cfg,octaveCorrection:false,forceMonophonic:true,
    meaning:'Single-voice frame decoding with pitch persistence and acoustic rest protection; uncalibrated heuristic.'};
}

export function frameRms(samples,sampleRate,times){
  return times.slice(0,-1).map((t,i)=>{
    const a=Math.max(0,Math.floor(t*sampleRate)),b=Math.min(samples.length,Math.ceil(times[i+1]*sampleRate));
    let sum=0;for(let j=a;j<b;j++)sum+=samples[j]**2;
    return Math.sqrt(sum/Math.max(1,b-a));
  });
}
