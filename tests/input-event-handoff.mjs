import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = process.env.JAMMATE_INPUT_URL || 'http://127.0.0.1:4188';
const samples = path.resolve(import.meta.dirname, '../examples/input-event-handoff');
const browser = await chromium.launch({
  channel: process.env.JAMMATE_BROWSER_CHANNEL || 'msedge',
  args: ['--disable-webgl'],
});
try {
  const page = await browser.newPage({acceptDownloads: true});
  await page.addInitScript(() => window.addEventListener('jammate:input', event => {
    window.lastInput = event.detail;
  }));
  page.setDefaultTimeout(90000);
  const errors = [], generated = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (['/api/band', '/api/create', '/api/reference/input/render', '/api/reference/text/turn',
         '/api/reference/rhythm/create'].includes(new URL(request.url()).pathname)) generated.push(request.url());
  });
  async function download(selector) {
    const [file] = await Promise.all([page.waitForEvent('download'), page.locator(selector).click()]);
    return readFile(await file.path());
  }
  async function readEvent(selector) {
    const event = JSON.parse(await download(selector));
    assert.deepEqual(await page.evaluate(() => window.lastInput), event);
    const reply = await page.request.post(base + '/api/validate', {data: event});
    assert.equal(reply.status(), 200, await reply.text());
    assert.deepEqual(await reply.json(), event);
    return event;
  }
  async function fixture(name) {
    return JSON.parse(await readFile(path.join(samples, name + '.input-event.json'), 'utf8'));
  }

  await page.goto(base + '/text-creation.html');
  const text = await readFile(path.join(samples, 'text.txt'), 'utf8');
  await page.locator('#creation-prompt').fill(text);
  const typed = await readEvent('#text-event');
  assert.equal(typed.payload.text, text);
  assert.equal(typed.source, 'text');
  assert.equal(typed.event_type, 'speech');
  assert.deepEqual(await readEvent('#text-event'), typed);

  await page.goto(base + '/free-rhythm.html');
  await page.locator('#file').setInputFiles(path.join(samples, 'rhythm.wav'));
  await page.locator('#analyze').click();
  const rhythm = await readEvent('#download-event');
  assert.deepEqual(rhythm.payload, (await fixture('rhythm')).payload);
  await page.locator('#hit-velocity').fill('45');
  await page.locator('#save-hit').click();
  const edited = await readEvent('#download-event');
  assert.notEqual(edited.event_id, rhythm.event_id);
  assert.deepEqual(edited.payload.raw_rhythm, rhythm.payload.raw_rhythm);
  assert.equal(edited.payload.rhythm.accents[0], 45 / 127);
  const midi = await download('#rhythm-mrt2-midi');
  assert.equal(midi.toString('ascii', 0, 4), 'MThd');

  await page.goto(base + '/optimized/dist/');
  await page.locator('#file').setInputFiles(path.join(samples, 'humming.wav'));
  await page.locator('#convert').click();
  const hum = await readEvent('#download-event');
  const notes = hum.payload.melody.notes, expected = (await fixture('humming')).payload.melody.notes;
  assert.deepEqual(notes.map(n => n.pitchMidi), expected.map(n => n.pitchMidi));
  notes.forEach((n, i) => {
    assert(Math.abs(n.startSec - expected[i].startSec) < .05);
    assert(Math.abs(n.durationSec - expected[i].durationSec) < .05);
  });
  assert.deepEqual(errors, []);
  assert.deepEqual(generated, []);
  console.log('PASS: three browser inputs downloaded and validated; %d notes, %d hits; edited MIDI exported.',
    notes.length, rhythm.payload.rhythm.onset_seconds.length);
} finally {
  await browser.close();
}
