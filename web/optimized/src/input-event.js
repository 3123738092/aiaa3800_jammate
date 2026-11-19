// The same team Contract is downloaded and submitted. MIDI stays an export.
export function hummingEvent(document, {sessionId, startedAt = 0, eventId}) {
  const reviewed = document.results.cleaned.userEdited === true;
  return {schema_version:reviewed ? '1.4.0' : '1.2.0', event_id:eventId, session_id:sessionId, source:'humming', event_type:'musical_phrase',
    started_at:startedAt, ended_at:startedAt + document.source.durationSec, confidence:null,
    payload:{melody:JSON.parse(JSON.stringify(document))},
    provenance:{producer:'melody_v1', source_id:document.id, source_schema_version:document.schemaVersion}};
}
