// Input-only regressions against the real local API. No Harness/MRT2 generation.
// PLAYWRIGHT_MODULE_PATH and JAMMATE_INPUT_URL can select another installation.
import assert from 'node:assert/strict';
import {after, before, test} from 'node:test';
import {createRequire} from 'node:module';
import {mkdir, readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = process.env.JAMMATE_INPUT_URL || 'http://127.0.0.1:4188';
const out = path.resolve(process.env.RHYTHM_TEST_OUT || path.join(root, 'tmp/rhythm-input-20261118'));
let browser;

before(async () => {
  await mkdir(out, {recursive: true});
  browser = await chromium.launch({channel: process.env.JAMMATE_BROWSER_CHANNEL || 'msedge', headless: true});
});
after(async () => { await browser?.close(); });

async function open(t, audioPath = process.env.RHYTHM_RECORDING) {
  const page = await browser.newPage({viewport: {width: 1280, height: 1000}, acceptDownloads: true});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await page.close(); assert.deepEqual(errors, [], 'no browser exceptions'); });
  // Observe real Web Audio nodes without replacing synthesis or scheduling.
  await page.addInitScript(() => {
    window.rhythmAudio = [];
    const create = AudioContext.prototype.createOscillator;
    AudioContext.prototype.createOscillator = function () {
      const node = create.call(this), start = node.start.bind(node), stop = node.stop.bind(node);
      const record = {start: null, stops: []};
      window.rhythmAudio.push(record);
      node.start = at => { record.start = at; return start(at); };
      node.stop = at => { record.stops.push(at ?? this.currentTime); return stop(at); };
      return node;
    };
  });
  await page.goto(`${base}/free-rhythm.html`);
  if (audioPath) await page.locator('#file').setInputFiles(audioPath);
  else await page.click('#example');
  await page.waitForFunction(() => !document.getElementById('analyze').disabled);
  await page.click('#analyze');
  await page.locator('#result').waitFor({timeout: 90000});
  await midiReady(page);
  return page;
}

async function midiReady(page) {
  await page.waitForFunction(() => !document.getElementById('rhythm-mrt2-midi').disabled, null, {timeout: 15000});
}
async function currentEvent(page) {
  return page.locator('#download-event').evaluate(async link => (await fetch(link.href)).json());
}
async function seek(page, seconds) {
  await page.locator('#seek').evaluate((slider, value) => {
    slider.value = String(value);
    slider.dispatchEvent(new Event('input', {bubbles: true}));
  }, seconds);
}
async function download(page, selector, filename) {
  const pending = page.waitForEvent('download');
  await page.click(selector);
  const result = await pending, target = path.join(out, filename);
  await result.saveAs(target);
  return target;
}
function readMidi(filename) {
  const python = process.env.JAMMATE_TEST_PYTHON || (process.platform === 'win32' ? path.join(root, '.venv/Scripts/python.exe') : 'python3');
  const result = spawnSync(python, ['-c', `import json, sys, mido
midi = mido.MidiFile(sys.argv[1])
now = 0.0
hits = []
for message in midi:
    now += message.time
    if message.type == 'note_on' and message.velocity:
        hits.append(dict(time=now, velocity=message.velocity, note=message.note, channel=message.channel))
print(json.dumps(dict(hits=hits, duration=now)))`, filename], {encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}
async function verifyExports(page, label, raw) {
  await midiReady(page);
  const event = JSON.parse(await readFile(await download(page, '#download-event', `${label}.json`), 'utf8'));
  const midi = readMidi(await download(page, '#rhythm-mrt2-midi', `${label}.mid`));
  assert.deepEqual(event, await currentEvent(page));
  assert.deepEqual(event.payload.raw_rhythm, raw, 'edits preserve detection evidence');
  const {onset_seconds: times, accents} = event.payload.rhythm;
  assert.equal(midi.hits.length, times.length, 'export contains precisely the edited hits');
  assert(Math.abs(midi.duration - event.payload.audio_duration_s) < 1 / 48000);
  midi.hits.forEach((hit, i) => {
    assert(Math.abs(hit.time - times[i]) <= 1 / 48000, 'MIDI onset matches edited seconds');
    assert.equal(hit.velocity, Math.max(1, Math.round(accents[i] * 127)));
    assert.equal(hit.channel, 9);
    assert.equal(hit.note, 36);
  });
  await seek(page, 0);
  const before = await page.evaluate(() => window.rhythmAudio.length);
  await page.click('#play-midi');
  await page.waitForFunction(() => document.getElementById('play-midi').textContent.includes('暂停'));
  const voices = await page.evaluate(index => window.rhythmAudio.slice(index), before);
  assert.equal(voices.length, times.length, 'audition schedules exactly the edited hits');
  voices.forEach((voice, i) => assert(Math.abs((voice.start - voices[0].start) - (midi.hits[i].time - midi.hits[0].time)) < 1e-8));
  await page.click('#play-midi');
  return {event, midi};
}

test('dense hits remain separate and individually selectable on desktop and mobile', async t => {
  const page = await open(t);
  // Put three existing hits 60 ms apart. Recognition and export remain real.
  for (const [index, time] of [[0, .1], [1, .16], [2, .22]]) {
    await page.selectOption('#hit-select', String(index));
    await page.fill('#hit-time', String(time));
    await page.click('#save-hit');
  }
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 1000});
    const bars = await page.locator('.rhythm-hit > span').evaluateAll(nodes => nodes.slice(0, 3).map(node => {
      const box = node.getBoundingClientRect();
      return {left: box.left, right: box.right, center: (box.left + box.right) / 2, bottom: box.bottom};
    }));
    assert(bars[0].right < bars[1].left && bars[1].right < bars[2].left,
      `${width}px viewport: dense visible bars must not overlap: ${JSON.stringify(bars)}`);
    for (let i = 0; i < 3; i++) {
      await page.locator('.rhythm-hit').nth(i).click();
      assert.equal(await page.locator('#hit-select').inputValue(), String(i));
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page-wide overflow');
    await page.screenshot({path: path.join(out, `dense-${width}.png`), fullPage: true});
  }
  const beforeZoom = await currentEvent(page);
  await page.selectOption('#timeline-zoom', '8');
  assert(await page.locator('#rhythm-viewport').evaluate(node => node.scrollWidth > node.clientWidth * 7));
  await page.locator('.rhythm-hit').last().click();
  assert(await page.locator('#rhythm-viewport').evaluate(node => node.scrollLeft > 0));
  const selection = await page.evaluate(() => {
    const viewport = document.getElementById('rhythm-viewport');
    const bounds = viewport.getBoundingClientRect();
    const cursor = document.getElementById('rhythm-cursor').getBoundingClientRect();
    return {left: bounds.left, right: bounds.right, cursor: cursor.left, scroll: viewport.scrollLeft};
  });
  assert(selection.cursor >= selection.left && selection.cursor <= selection.right, JSON.stringify(selection));
  assert.deepEqual(await currentEvent(page), beforeZoom, 'zoom and selection do not edit the event');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.selectOption('#hit-select', '1');
  await page.screenshot({path: path.join(out, 'dense-mobile-zoom.png'), fullPage: true});
});

test('deleting a hit stops the unchanged source recording before it can play that hit', async t => {
  const page = await open(t);
  const original = await currentEvent(page);
  await page.selectOption('#hit-select', String(original.payload.rhythm.onset_seconds.length - 1));
  await page.locator('#original').evaluate(async audio => { audio.currentTime = 0; await audio.play(); });
  assert.equal(await page.locator('#original').evaluate(audio => audio.paused), false);
  await page.click('#delete-hit');
  assert.equal(await page.locator('#original').evaluate(audio => audio.paused), true,
    'the source recording must stop when editing; otherwise a deleted hit is still audible');
  const exported = await verifyExports(page, 'delete-last', original.payload.raw_rhythm);
  assert.deepEqual(exported.event.payload.rhythm.onset_seconds, original.payload.rhythm.onset_seconds.slice(0, -1));
});

test('edit, delete during audition, delete all and undo keep audition/MIDI/InputEvent in sync', async t => {
  const page = await open(t);
  const original = await currentEvent(page), raw = original.payload.raw_rhythm;
  await page.fill('#hit-time', '.5');
  await page.fill('#hit-velocity', '43');
  await page.click('#save-hit');
  const changed = await verifyExports(page, 'moved-and-softened', raw);
  assert.equal(changed.event.payload.rhythm.onset_seconds[0], .5);
  assert.equal(changed.midi.hits[0].velocity, 43);
  const count = changed.midi.hits.length;
  await page.selectOption('#hit-select', String(count - 1));
  await seek(page, 0);
  const start = await page.evaluate(() => window.rhythmAudio.length);
  await page.click('#play-midi');
  await page.waitForFunction(n => window.rhythmAudio.length === n, start + count);
  await page.click('#delete-hit');
  const stopped = await page.evaluate(index => window.rhythmAudio.slice(index), start);
  assert.equal(stopped.length, count);
  assert(stopped.every(voice => voice.stops.length >= 2), 'editing cancels every previously scheduled voice');
  await verifyExports(page, 'delete-during-audition', raw);
  while (await page.locator('.rhythm-hit').count()) await page.click('#delete-hit');
  const empty = await currentEvent(page);
  assert.equal(empty.payload.status, 'no_onsets');
  const silence = await verifyExports(page, 'all-deleted', raw);
  assert.deepEqual(silence.midi.hits, []);
  await page.click('#undo-hit');
  const restored = await verifyExports(page, 'undo-last-deletion', raw);
  assert.equal(restored.midi.hits.length, 1);
  assert.notEqual(restored.event.event_id, empty.event_id);
});
