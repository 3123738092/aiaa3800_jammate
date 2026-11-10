import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';

const {chromium} = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const {Midi} = createRequire(new URL('../web/optimized/package.json', import.meta.url))('@tonejs/midi');
const base = process.env.INPUT_LAB_URL || 'http://127.0.0.1:4188';
const out = path.resolve(import.meta.dirname, '../tmp/reference-browser');
await mkdir(out, {recursive: true});
const browser = await chromium.launch({channel: 'msedge', headless: true});
const page = await browser.newPage({acceptDownloads: true, viewport: {width: 390, height: 844}});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const turnURL = base + '/api/reference/text/turn';
const generate = page.locator('#text-generate');
const prompt = page.locator('#creation-prompt');
const output = page.locator('#text-output');

async function download(selector, name) {
  const ready = page.waitForEvent('download');
  await page.locator(selector).click();
  const file = path.join(out, name);
  await (await ready).saveAs(file);
  return readFile(file);
}

async function readWav(player) {
  return player.evaluate(async element => {
    const context = new AudioContext();
    try {
      const clip = await context.decodeAudioData(await (await fetch(element.src)).arrayBuffer());
      if (!clip.getChannelData(0).some(sample => Math.abs(sample) > .001)) {
        throw new Error('Generated WAV is silent');
      }
      return clip.duration;
    } finally {
      await context.close();
    }
  });
}

async function submit(text, expectedStatus) {
  await prompt.fill(text);
  const received = page.waitForResponse(turnURL);
  await generate.click();
  const response = await received;
  const result = await response.json();
  assert.equal(response.status(), expectedStatus, result.error);
  assert.equal(response.request().postDataJSON().text, text);
  return result;
}

try {
  await page.goto(base);
  await page.locator('a[href="rhythm.html"]').click();
  await page.locator('#example').click();
  await page.locator('#analyze').click();
  await page.locator('[data-render]').click();
  await page.locator('[data-output]').waitFor({state: 'visible'});
  await page.goto(base);
  await page.locator('a[href="text-creation.html"]').click();
  assert.equal(await prompt.inputValue(), '');
  assert(await output.isHidden(), 'text opens an independent work');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));

  const blocked = await page.request.post(turnURL, {
    data: {}, headers: {Origin: 'https://unrelated.invalid'}
  });
  assert.equal(blocked.status(), 403);
  await page.waitForFunction(() => !document.querySelector('#text-profile option[value="acoustic"]').disabled);
  await page.locator('details summary').click();
  await page.locator('#text-profile').selectOption('acoustic');

  const first = await submit('来一段，慢一点', 200);
  await output.waitFor({state: 'visible'});
  assert.equal(first.agent, 'rule');
  assert.equal(first.turn, 1);
  assert.equal(first.tempo_bpm, 92);
  assert.match(first.reference_commit, /^a63a0f5/);
  assert.equal(first.plan.trigger_event_ids.length, 1);
  assert(await readWav(page.locator('#text-audio')) > 10);
  await download('#text-wav', 'text-first.wav');
  const initial = new Midi(await download('#text-midi', 'text-first.mid'));
  assert(initial.tracks.some(track => track.channel === 2 && track.notes.length), 'bass before the edit');
  assert(initial.tracks.some(track => track.channel === 1 && track.instrument.number === 0 && track.notes.length));
  await writeFile(path.join(out, 'text-first.plan.json'), JSON.stringify(first.plan, null, 2));

  let completed;
  await page.route(turnURL, async route => {
    const actual = await route.fetch();
    completed = await actual.json();
    assert.equal(actual.status(), 200, completed.error);
    await route.abort();
  }, {times: 1});
  await prompt.fill('去掉贝斯');
  await generate.click();
  await page.waitForFunction(() => document.querySelector('#text-status').textContent.includes('生成失败'));
  assert(await output.isVisible(), 'a lost response keeps the last playable clip');
  const stale = await submit('鼓少一点', 400);
  assert.match(stale.error, /已生成更新的段落/);
  const second = await submit('去掉贝斯', 200);
  assert.equal(second.request_id, completed.request_id);
  assert.equal(second.turn, 2);
  assert.equal(second.session_id, first.session_id);
  assert.equal(second.start_bar, first.bars);
  await page.waitForFunction(() => document.querySelector('#text-status').textContent.includes('第 2 段'));
  assert(await readWav(page.locator('#text-audio')) > 10);
  await download('#text-wav', 'text-adjusted.wav');
  const changed = new Midi(await download('#text-midi', 'text-adjusted.mid'));
  assert(!changed.tracks.some(track => track.channel === 2 && track.notes.length), 'bass removed by original agent');
  assert(Math.abs(changed.header.tempos[0].bpm - 92) < .001);
  await writeFile(path.join(out, 'text-adjusted.plan.json'), JSON.stringify(second.plan, null, 2));

  const rejected = await submit('变成一只猫', 400);
  assert.match(rejected.error, /无法执行/);
  assert(await output.isVisible(), 'unsupported prompt keeps the successful output');
  await page.locator('#new-work').click();
  assert(await output.isHidden());
  assert(await page.locator('#text-bpm').isEnabled());

  await page.goto(base);
  await page.getByText('试听队友的风格预设', {exact: true}).click();
  const styles = {};
  for (const name of ['lofi', 'warm_funk', 'indie_rock']) {
    await page.locator('#reference-style').selectOption(name);
    const received = page.waitForResponse(base + '/api/reference/style');
    await page.locator('[data-render]').click();
    const response = await received;
    const result = await response.json();
    assert.equal(response.status(), 200, result.error);
    assert.equal(result.style, name);
    assert.equal(result.tempo_bpm, 100);
    await page.locator('[data-output]').waitFor({state: 'visible'});
    await readWav(page.locator('[data-output] audio'));
    await download('[data-wav]', name + '.wav');
    const midi = new Midi(await download('[data-midi]', name + '.mid'));
    styles[name] = midi.tracks.find(track => track.channel === 9).notes.map(note => [note.midi, note.ticks]);
  }
  assert.equal(new Set(Object.values(styles).map(JSON.stringify)).size, 3);
  assert.deepEqual(errors, []);
  const report = {textTurns: [first.turn, second.turn], textTempo: second.tempo_bpm, renderer: first.renderer,
    provider: first.agent, settings: 'acoustic', responseRetryDeduped: true,
    independentFromRhythm: true, styleSamples: Object.keys(styles), styleTempo: 100, pageErrors: errors};
  await writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}
