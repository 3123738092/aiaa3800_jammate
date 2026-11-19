import { noteFramesToTime, outputToNotesPoly } from '@spotify/basic-pitch';
import { SAMPLE_RATE } from './audio-pipeline.js';
import { DECODING, standardizeNotes } from './melody.js';
import { decodeHummingRevisions } from './humming-d.js';
import { filterShortHummingNotes } from './humming-duration-filter.js';

export function transcribeHummingFrames(samples,frames,onsets,{comparePrevious=false,strictFilter=true}={}) {
  const times=noteFramesToTime(Array.from({length:frames.length+1},(_,startFrame)=>({
    startFrame,durationFrames:1,pitchMidi:60,amplitude:1
  }))).map(note=>note.startTimeSeconds);
  const config=DECODING;
  // Basic Pitch mutates its decoder inputs. Preserve model evidence for the rule.
  const events=outputToNotesPoly(frames.map(row=>row.slice()),onsets.map(row=>row.slice()),
    config.onsetThreshold,config.frameThreshold,config.minimumNoteFrames,config.inferOnsets,
    config.maximumFrequencyHz,config.minimumFrequencyHz,config.melodiaTrick,config.energyToleranceFrames);
  const decoded=noteFramesToTime(events).map((note,i)=>{
    const first=events[i].startFrame;
    const scores=onsets.slice(Math.max(0,first-1),first+2).map(row=>row[note.pitchMidi-21]);
    return {...note,onsetActivation:scores.length?Math.max(...scores):null};
  });
  const raw=standardizeNotes(decoded,samples.length/SAMPLE_RATE);
  const revisions=decodeHummingRevisions(samples,{frames,onsets,times},raw,comparePrevious,{legato:strictFilter});
  if (strictFilter) revisions.cleaned=filterShortHummingNotes(samples,revisions.cleaned,raw);
  return {raw,...revisions};
}
