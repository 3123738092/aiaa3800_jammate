// Additive adapter. The baseline model and cleanup algorithm are unchanged.
export function hummingEvent(document, {sessionId, startedAt = 0, eventId}) {
  return {event_id:eventId, session_id:sessionId, source:'humming', event_type:'musical_phrase',
    started_at:startedAt, ended_at:startedAt + document.source.durationSec, confidence:null,
    payload:{timebase:'seconds_from_recording_start', status:document.notes.length?'ready':'no_notes',
      tempo_bpm:null, notes:document.notes.map(n=>({pitch_midi:n.pitchMidi,start_sec:n.startSec,
        duration_sec:n.durationSec,velocity:n.velocity01})), audio:document.source,
      analysis:{backend:'basic-pitch',variant:document.selectedResult,
        confidence_meaning:'uncalibrated; activations are not probabilities'}}};
}
