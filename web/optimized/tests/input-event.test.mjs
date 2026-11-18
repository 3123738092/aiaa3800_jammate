import test from 'node:test';import assert from 'node:assert/strict';
import {hummingEvent}from'../src/input-event.js';
import {buildExports,createMelodyDocument}from'../src/melody.js';
test('selected variant controls event notes and status; session offset leaves relative notes unchanged',()=>{
 const notes=[{id:'h1',pitchMidi:60,startSec:.4,durationSec:1,velocity01:.7}];
 const doc=createMelodyDocument({id:'clip-a',durationSec:2},{},[],{notes,edits:[]},10);
 const selected=buildExports(doc,'cleaned').document;
 const event=hummingEvent(selected,{sessionId:'s',startedAt:23,eventId:'e'});
 assert.equal(event.schema_version,'1.2.0');
 assert.equal(selected.status,'ready');assert.equal(event.payload.melody.status,'ready');
 assert.equal(event.started_at,23);assert.equal(event.ended_at,25);assert.equal(event.payload.melody.notes[0].startSec,.4);
 assert.equal(event.confidence,null);assert.deepEqual(event.payload.melody,selected);
 const raw=buildExports(doc,'raw').document;
 assert.equal(raw.status,'no_notes');assert.equal(hummingEvent(raw,{sessionId:'s',eventId:'r'}).payload.melody.status,'no_notes');
});

test('event preserves selected evidence as a snapshot, without inventing confidence or tempo',()=>{
 const notes=[{id:'h1',pitchMidi:60,startSec:.4,durationSec:1,velocity01:.7}];
 const document=buildExports(createMelodyDocument({id:'clip-b',durationSec:2},{},notes,{notes,edits:[]},10),'cleaned').document;
 const event=hummingEvent(document,{sessionId:'s',eventId:'e'});
 document.notes[0].pitchMidi=65;
 assert.equal(event.payload.melody.notes[0].pitchMidi,60);
 assert.equal(event.confidence,null);
 assert.equal(event.provenance.source_id,'clip-b');
 assert.equal(event.source,'humming');assert.equal(event.event_type,'musical_phrase');
 assert.equal('strategy' in event,false);assert.equal('tempo_bpm' in event.payload,false);
});
