import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';

const {chromium} = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const {Midi} = createRequire(new URL('../web/optimized/package.json', import.meta.url))('@tonejs/midi');
const base = process.env.INPUT_LAB_URL || 'http://127.0.0.1:4187';
const out = path.resolve(import.meta.dirname, '../tmp/creation-text');
await mkdir(out, {recursive: true});
const browser = await chromium.launch({channel: 'msedge', headless: true});
const page = await browser.newPage({acceptDownloads: true, viewport: {width: 390, height: 844}});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const createURL = base + '/api/create';
const generate = page.locator('[data-render]');
const prompt = page.locator('#creation-prompt');
const output = page.locator('[data-output]');

async function generateMusic(text, expectedStatus) {
  const received = page.waitForResponse(createURL);
  await generate.click();
  const response = await received;
  assert.deepEqual(response.request().postDataJSON(), {text});
  const result = await response.json();
  assert.equal(response.status(), expectedStatus, result.error);
  return result;
}

async function download(selector, name) {
  const ready = page.waitForEvent('download');
  await page.locator(selector).click();
  const file = path.join(out, name);
  await (await ready).saveAs(file);
  return readFile(file);
}

try {
  await page.goto(base);
  await page.locator('a[href="rhythm.html"]').click();
  await page.locator('#example').click();
  await page.locator('#analyze').click();
  await generate.click();
  await output.waitFor({state: 'visible'});

  await page.goto(base);
  await page.locator('a[href="text-creation.html"]').click();
  assert.equal(await prompt.inputValue(), '');
  assert(await output.isHidden(), 'the rhythm output is not carried into text creation');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile layout');
  const slow = '来一段，慢一点';
  const fast = '来一段，快一点';
  await page.getByRole('button', {name: '试试慢一点'}).click();
  await page.route(createURL, route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({error: '暂不可用'})
  }), {times: 1});
  await generateMusic(slow, 503);
  await page.waitForFunction(() => document.querySelector('[data-status]').textContent.includes('暂不可用'));
  assert(await generate.isEnabled());
  const slower = await generateMusic(slow, 200);
  await output.waitFor({state: 'visible'});
  assert.equal(slower.tempo_bpm, 92);
  assert.deepEqual(slower.plan.trigger_event_ids, [slower.event_id]);
  const wav = await download('[data-wav]', 'text.wav');
  assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
  const duration = await output.locator('audio').evaluate(async player => {
    const context = new AudioContext();
    try {
      const audio = await context.decodeAudioData(await (await fetch(player.src)).arrayBuffer());
      if (!audio.getChannelData(0).some(sample => Math.abs(sample) > .001)) {
        throw new Error('generated audio is silent');
      }
      return audio.duration;
    } finally {
      await context.close();
    }
  });
  assert(duration > 10);
  const midi = new Midi(await download('[data-midi]', 'text.mid'));
  assert(Math.abs(midi.header.tempos[0].bpm - 92) < .001);
  assert(midi.tracks.some(track => (track.channel === 4 || track.name === 'lead') && track.notes.length));
  const input = JSON.parse(await download('[data-event]', 'text.input.json'));
  assert.deepEqual(input, {text: slow});
  await writeFile(path.join(out, 'text.plan.json'), JSON.stringify(slower.plan, null, 2));

  const invalid = '来一段，变成一只猫';
  await prompt.fill(invalid);
  assert(await output.isHidden(), 'edited prompt discards the previous output');
  const rejected = await generateMusic(invalid, 400);
  assert.match(rejected.error, /指令未执行/);

  await page.getByRole('button', {name: '试试快一点'}).click();
  const faster = await generateMusic(fast, 200);
  await output.waitFor({state: 'visible'});
  assert.equal(faster.tempo_bpm, 108);
  assert.notEqual(faster.plan.session_id, slower.plan.session_id);
  const fastMidi = new Midi(Buffer.from(faster.midi, 'base64'));
  assert(Math.abs(fastMidi.header.tempos[0].bpm - 108) < .001);
  assert.deepEqual(errors, []);
  const report = {slowTempo: slower.tempo_bpm, fastTempo: faster.tempo_bpm,
    renderer: slower.renderer, wavDuration: duration, rejectedUnsupported: true,
    retrySucceeded: true, independentFromRhythm: true, pageErrors: errors};
  await writeFile(path.join(out, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
