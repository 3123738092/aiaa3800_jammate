import test from 'node:test';import assert from 'node:assert/strict';
import {hummingEvent}from'../src/input-event.js';
import {buildExports,createMelodyDocument}from'../src/melody.js';
test('selected variant controls event notes and status; session offset leaves relative notes unchanged',()=>{
 const notes=[{id:'h1',pitchMidi:60,startSec:.4,durationSec:1,velocity01:.7}];
 const doc=createMelodyDocument({id:'clip-a',durationSec:2},{},[],{notes,edits:[]},10);
 const selected=buildExports(doc,'cleaned').document;
 const event=hummingEvent(selected,{sessionId:'s',startedAt:23,eventId:'e'});
 assert.equal(selected.status,'ready');assert.equal(event.payload.status,'ready');
 assert.equal(event.started_at,23);assert.equal(event.ended_at,25);assert.equal(event.payload.notes[0].start_sec,.4);
 assert.equal(event.confidence,null);assert.equal(event.payload.tempo_bpm,null);
 const raw=buildExports(doc,'raw').document;
 assert.equal(raw.status,'no_notes');assert.equal(hummingEvent(raw,{sessionId:'s',eventId:'r'}).payload.status,'no_notes');
});
