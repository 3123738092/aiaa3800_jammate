import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';

const {chromium} = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const base = process.env.INPUT_LAB_URL || 'http://127.0.0.1:4221';
const browser = await chromium.launch({channel: 'msedge', args: [
  '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
  '--use-file-for-fake-audio-capture=' + resolve('web/rhythm-example.wav'),
]});
try {
  const page = await browser.newPage({permissions: ['microphone']});
  const errors = [], modes = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.SpeechRecognition = window.webkitSpeechRecognition = undefined;
  });
  let reply;
  await page.route('**/api/jam/opening-mic', async route => {
    const data = route.request().postDataJSON();
    assert.ok(Buffer.from(data.audio, 'base64').length > 1000);
    modes.push(data.mode);
    await route.fulfill({json: reply});
  });
  await page.goto(base + '/jam.html');
  const mic = page.locator('#opening-mic'), words = page.locator('#jam-opening-text');
  const confirm = page.locator('#opening-mic-confirm'), play = page.locator('#jam-start');
  assert.equal(await mic.count(), 1, 'opening microphone is present');
  async function take(result) {
    reply = result;
    await mic.click();
    await page.waitForFunction(() => document.querySelector('#opening-mic').getAttribute('aria-pressed') === 'true');
    await page.waitForTimeout(650);
    await mic.click();
  }
  for (const [kind, route] of [['humming', 'WAIT_HUMMING'], ['rhythm', 'WAIT_RHYTHM']]) {
    await take({route});
    const card = page.locator('[data-jam-input="' + kind + '"]');
    await card.getByRole('button', {name: '停止录制'}).waitFor();
    assert.ok(await card.evaluate(el => el.classList.contains('mic-target')));
    assert.ok(await mic.isDisabled());
    await page.waitForTimeout(700);
    await card.locator('[data-record]').click();
    await card.locator('audio[data-input-audio]:not([hidden])').waitFor();
  }
  await take({route: 'WAIT_TEXT'});
  await page.waitForFunction(() => document.querySelector('#opening-mic').getAttribute('aria-pressed') === 'true');
  reply = {route: 'SEND_TEXT', instruction: '慢一点，先别加鼓。'};
  await page.waitForTimeout(650);
  await mic.click();
  await confirm.waitFor();
  assert.equal(await words.inputValue(), reply.instruction);
  assert.ok(await play.isDisabled());
  await words.fill('慢一点，用钢琴。');
  await confirm.click();
  assert.equal(await words.inputValue(), '慢一点，用钢琴。');
  assert.ok(await play.isEnabled());
  await take({route: 'SEND_TEXT', instruction: '速度九十。'});
  await confirm.waitFor();
  assert.match(await words.inputValue(), /慢一点，用钢琴。.*速度九十。/s);
  await page.locator('#opening-mic-discard').click();
  assert.equal(await words.inputValue(), '慢一点，用钢琴。');
  await take({route: 'KEEP_LISTENING'});
  await page.locator('#opening-mic-status').filter({hasText:'没听清'}).waitFor();
  assert.deepEqual(modes, ['intent','intent','intent','dictation','intent','intent']);
  await page.evaluate(() => {
    window.SpeechRecognition = class {
      start() { this.onresult({results: [[{transcript:'先用钢琴'}]]}); }
      abort() {}
    };
  });
  await take({route: 'WAIT_TEXT'});
  await page.waitForFunction(() => document.querySelector('#jam-opening-text').value.includes('先用钢琴'));
  await page.waitForTimeout(650);
  reply = {route:'SEND_TEXT', instruction:'先用钢琴，速度九十。'};
  await mic.click();
  await confirm.waitFor();
  assert.match(await words.inputValue(), /先用钢琴，速度九十/);
  await confirm.click();
  const beforeFailure = await words.inputValue();
  await page.route('**/api/jam/opening-mic', r => r.fulfill({status: 503, json: {error: '服务暂不可用，请重试。'}}), {times: 1});
  await take({route:'WAIT_HUMMING'});
  await page.locator('#opening-mic-status').filter({hasText: '暂不可用'}).waitFor();
  assert.ok(await mic.isEnabled());
  assert.equal(await words.inputValue(), beforeFailure);

  for (const [kind, route, manualLabel] of [
    ['humming', 'USE_HUMMING', '哼唱录音'], ['rhythm', 'USE_RHYTHM', '拍手录音'],
  ]) {
    const card = page.locator('[data-jam-input="' + kind + '"]');
    const requestsBefore = modes.length;
    await take({route});
    await card.locator('[data-input-status]').filter({hasText: '总麦克风录音'}).waitFor();
    assert.ok(await card.locator('audio[data-input-audio]').isVisible());
    await card.locator('[data-clear]').click();
    await card.locator('[data-record]').click();
    const stop = card.getByRole('button', {name: '停止录制'});
    await stop.waitFor();
    await page.waitForTimeout(700);
    await stop.click();
    await card.locator('[data-input-status]').filter({hasText: manualLabel}).waitFor();
    assert.ok(await card.locator('audio[data-input-audio]').isVisible());
    assert.deepEqual(modes.slice(requestsBefore), ['intent']);
  }
  assert.deepEqual(errors, []);
  console.log('Browser passed: automatic and manual recording, opening audio reuse, dictation, editable confirmation, direct instructions, and uncertain audio.');
} finally {
  await browser.close();
}
