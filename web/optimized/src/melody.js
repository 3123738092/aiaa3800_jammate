import midiPackage from '@tonejs/midi';
const { Midi } = midiPackage;

export const APP_VERSION = '1.1.0';
export const DECODING = Object.freeze({onsetThreshold:.25, frameThreshold:.25, minimumNoteFrames:9,
  inferOnsets:true, maximumFrequencyHz:1046.502, minimumFrequencyHz:65.406,
  melodiaTrick:true, energyToleranceFrames:11, pitchMin:36, pitchMax:84});
export const CLEANUP = Object.freeze({version:'1.0.0', maximumGapSec:.06,
  maximumOverlapSec:.01, maximumFragmentSec:.18, minimumPreviousSec:.25, maximumOnsetActivation:.35});
export const TIMBRES = Object.freeze({
  soft:{label:'柔和合成器', program:80, oscillator:'sine'},
  bright:{label:'明亮合成器', program:81, oscillator:'sawtooth'},
  pluck:{label:'短促拨弦', program:24, oscillator:'triangle'},
});
export const VELOCITY = 90 / 127;
export const MIDI_REFERENCE_BPM = 120;
const round = value => Math.round(value * 1e6) / 1e6;

export function standardizeNotes(decoded, durationSec, offsetSec = 0) {
  return decoded.map((note, index) => {
    const modelStart = note.startTimeSeconds + offsetSec;
    const startSec = Math.max(offsetSec, modelStart);
    const endSec = Math.min(durationSec, modelStart + note.durationSeconds);
    return {id:`n${index+1}`, pitchMidi:note.pitchMidi, startSec:round(startSec),
      durationSec:round(endSec-startSec), velocity01:VELOCITY,
      modelActivation:Number.isFinite(note.amplitude)?note.amplitude:null,
      onsetActivation:Number.isFinite(note.onsetActivation)?note.onsetActivation:null,
      sourceNoteIds:[`n${index+1}`]};
  }).filter(note => Number.isInteger(note.pitchMidi) && note.pitchMidi >= DECODING.pitchMin &&
    note.pitchMidi <= DECODING.pitchMax && Number.isFinite(note.startSec) &&
    Number.isFinite(note.durationSec) && note.durationSec > 0 && note.startSec < durationSec)
    .sort((a,b) => a.startSec-b.startSec || a.pitchMidi-b.pitchMidi);
}

export function cleanNotes(raw) {
  const notes = [], edits = [];
  for (const original of raw) {
    const note = {...original, sourceNoteIds:[...original.sourceNoteIds]};
    const previous = notes.findLast(item => item.pitchMidi === note.pitchMidi);
    const gap = previous ? note.startSec - previous.startSec - previous.durationSec : Infinity;
    // Missing onset evidence is not evidence of a weak onset. Never merge it.
    if (previous && gap >= -CLEANUP.maximumOverlapSec && gap <= CLEANUP.maximumGapSec &&
      note.durationSec <= CLEANUP.maximumFragmentSec && previous.durationSec >= CLEANUP.minimumPreviousSec &&
      note.onsetActivation !== null && note.onsetActivation < CLEANUP.maximumOnsetActivation) {
      previous.durationSec = round(Math.max(previous.startSec+previous.durationSec, note.startSec+note.durationSec)-previous.startSec);
      previous.sourceNoteIds.push(...note.sourceNoteIds);
      edits.push({type:'merge_weak_tail', targetId:previous.id, sourceId:note.id, gapSec:round(gap), onsetActivation:note.onsetActivation});
    } else notes.push(note);
  }
  return {notes, edits, policy:{...CLEANUP}, octaveCorrection:false, forceMonophonic:false};
}

export function buildExports(document, variant = 'cleaned', timbreId = 'soft') {
  if (!['raw','cleaned'].includes(variant) || !TIMBRES[timbreId]) throw new Error('无效的回放设置。');
  const notes = document.results[variant].notes;
  const midi = new Midi();
  midi.name = `JamMate ${variant}`;
  midi.header.setTempo(MIDI_REFERENCE_BPM); // Encoding reference only. No detected/assigned musical tempo.
  const track = midi.addTrack(); track.name = 'Humming'; track.instrument.number = TIMBRES[timbreId].program;
  for (const note of notes) track.addNote({midi:note.pitchMidi,time:note.startSec,duration:note.durationSec,velocity:note.velocity01});
  // This library ends note tracks at their last event. A conductor-track marker
  // retains the original recording end without adding an audible/control event.
  midi.header.meta.push({type:'marker',text:'JamMate original audio end',ticks:midi.header.secondsToTicks(document.source.durationSec)});
  const bytes = midi.toArray();
  const exported = new Midi(bytes);
  return {bytes, playbackNotes:exported.tracks.flatMap(item=>item.notes),
    document:{...document,selectedResult:variant,notes,
      playback:{timbreId,gmProgramNumber:TIMBRES[timbreId].program,gmProgramNumberBase:0,
        velocityPolicy:'fixed_audition_level', velocity01:VELOCITY,
        timbreNote:'Browser synthesizer approximations; external MIDI instruments may sound different.'},
      midiExport:{referenceBpm:MIDI_REFERENCE_BPM,tempoMeaning:'serialization_reference_only',
        ppq:midi.header.ppq,timeRoundingToleranceSec:60/MIDI_REFERENCE_BPM/midi.header.ppq,
        originalEndMarker:'JamMate original audio end',timeSignature:null,quantized:false}}
  };
}

export function createMelodyDocument(source, input, raw, cleaned, elapsedMs) {
  return {schemaVersion:'1.0.0',appVersion:APP_VERSION,id:source.id,
    createdAt:new Date().toISOString(),status:raw.length?'ready':'no_notes',source,
    timebase:{unit:'seconds',origin:'original_audio_start'},
    tempo:{bpm:null,source:'unknown',mode:'free',quantized:false},
    key:null,timeSignature:null,input,
    model:{name:'@spotify/basic-pitch',version:'1.0.1',decoding:{...DECODING},
      scoreMeaning:'Mean frame activation and onset activation; not calibrated confidence or measured loudness.',
      pitchBendsIncluded:false},
    analysisElapsedMs:Math.round(elapsedMs),
    results:{raw:{notes:raw,meaning:'Decoded model notes with validity and original-timeline bounds checks; no musical cleanup.'},cleaned}};
}
