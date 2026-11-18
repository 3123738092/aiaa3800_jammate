// Conservative, single-voice note cleanup. These are model activations, not
// calibrated confidence or loudness. Never remove a note for duration alone.
export const ARTIFACT_FILTER = Object.freeze({version:'1.0.0',maximumDurationSec:.22,
  maximumActivation:.32,strongOnset:.65,maximumRelativeActivation:.65,
  minimumAnchorDurationSec:.28,minimumAnchorActivation:.24,maximumAnchorGapSec:.12});

export function filterHummingArtifacts(decoded) {
  const cfg=ARTIFACT_FILTER,notes=[],removedNotes=[...(decoded.removedNotes??[])],edits=[...decoded.edits];
  for(let i=0;i<decoded.notes.length;i++){
    const note=decoded.notes[i];
    // Immediate neighbors only: a loud phrase elsewhere must not erase a quiet
    // phrase. Assess every candidate against the unchanged input to avoid cascades.
    const anchors=[decoded.notes[i-1],decoded.notes[i+1]].filter(neighbor=>{
      if(!neighbor||neighbor.durationSec<cfg.minimumAnchorDurationSec||
        !Number.isFinite(neighbor.modelActivation)||neighbor.modelActivation<cfg.minimumAnchorActivation)return false;
      const gap=neighbor.startSec<note.startSec
        ?note.startSec-neighbor.startSec-neighbor.durationSec
        :neighbor.startSec-note.startSec-note.durationSec;
      return gap>=-.01&&gap<=cfg.maximumAnchorGapSec;
    });
    const anchorActivation=anchors.length?Math.max(...anchors.map(n=>n.modelActivation)):null;
    const suspect=note.durationSec<=cfg.maximumDurationSec&&
      Number.isFinite(note.modelActivation)&&note.modelActivation<cfg.maximumActivation&&
      Number.isFinite(note.onsetActivation)&&note.onsetActivation<cfg.strongOnset&&
      anchorActivation!==null&&note.modelActivation<anchorActivation*cfg.maximumRelativeActivation;
    if(suspect){
      removedNotes.push(structuredClone(note));
      edits.push({type:'remove_short_artifact',sourceId:note.id,startSec:note.startSec,
        durationSec:note.durationSec,modelActivation:note.modelActivation,onsetActivation:note.onsetActivation,
        anchorIds:anchors.map(n=>n.id),anchorActivation,
        reason:'Short, weak pitch support relative to adjacent stable notes, without a strong onset; heuristic, not ground truth.'});
    }else notes.push(structuredClone(note));
  }
  return {...decoded,notes,removedNotes,edits,policy:{...decoded.policy,artifactFilter:{...cfg}},
    meaning:'Single-voice humming with conservative short-artifact filtering; original timing retained, no tempo quantization; uncalibrated heuristic.'};
}
