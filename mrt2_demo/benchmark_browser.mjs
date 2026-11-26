// Measure playback in a local Chromium browser against a real JamMate service.
import {execFileSync, spawn} from 'node:child_process';
import {mkdir, mkdtemp, readFile, writeFile} from 'node:fs/promises';
import {basename, join} from 'node:path';
import {setTimeout as wait} from 'node:timers/promises';

const [base, out, renderer = 'synth', bars = '105', debug = 'closed'] = process.argv.slice(2);
if (!base || !out || !Number.isInteger(+bars) || +bars < 1) {
  throw Error('Usage: node mrt2_demo/benchmark_browser.mjs URL OUT [renderer] [bars] [open|closed]');
}
await mkdir(out, {recursive: true});
const profile = await mkdtemp(join(process.env.TMPDIR || '/tmp', 'jammate-bench-'));
const executable = process.env.JAMMATE_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const chrome = spawn(executable, ['--headless', '--disable-extensions', '--mute-audio',
  '--no-first-run', '--no-proxy-server', '--remote-debugging-port=0', '--user-data-dir=' + profile,
  'about:blank'], {stdio: 'ignore'});

async function until(check, seconds) {
  const end = Date.now() + seconds * 1000;
  while (Date.now() < end) {
    const result = await check();
    if (result) return result;
    await wait(100);
  }
  throw Error('Timed out waiting for browser playback or export');
}

const report = {renderer, debug, bars_requested: +bars, status: 'running', observations: [], errors: [],
  browser: 'Chromium headless / Web Audio clock', speaker_output_measured: false};
let socket;
try {
  const port = await until(async () => {
    try { return (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; }
    catch { return null; }
  }, 10);
  const target = await until(async () => {
    const tabs = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
    return tabs.find(tab => tab.type === 'page' && tab.url === 'about:blank');
  }, 5);
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once: true});
    socket.addEventListener('error', reject, {once: true});
  });
  const pending = new Map(), requests = new Map(), httpTimes = {};
  let id = 0, startRequest = null;
  socket.addEventListener('message', ({data}) => {
    const {id, result, error, method, params} = JSON.parse(data);
    if (id) {
      pending.get(id)?.({result, error});
      pending.delete(id);
    } else if (method === 'Runtime.exceptionThrown') {
      report.errors.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text);
    } else if (method === 'Network.requestWillBeSent' && params.request.url.includes('/api/jam/')) {
      const url = new URL(params.request.url);
      requests.set(params.requestId, {name: url.pathname.split('/').at(-1), at: params.timestamp});
      if (url.pathname === '/api/jam/start') startRequest = params.requestId;
    } else if (method === 'Network.responseReceived' && requests.has(params.requestId)) {
      const {name, at} = requests.get(params.requestId);
      (httpTimes[name] ??= []).push((params.timestamp - at) * 1000);
      requests.delete(params.requestId);
      if (params.response.status >= 400) report.errors.push(name + ' HTTP ' + params.response.status);
    }
  });
  async function call(method, params = {}) {
    const key = ++id;
    const reply = new Promise(resolve => pending.set(key, resolve));
    socket.send(JSON.stringify({id: key, method, params}));
    const {result, error} = await reply;
    if (error) throw Error(error.message);
    return result;
  }
  async function evaluate(expression) {
    const response = await call('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true, userGesture: true});
    if (response.exceptionDetails) throw Error(response.exceptionDetails.exception?.description);
    return response.result.value;
  }
  await call('Runtime.enable');
  await call('Network.enable');
  await call('Performance.enable');
  await call('Page.enable');
  await call('Page.addScriptToEvaluateOnNewDocument', {source: `
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = undefined;
    window.benchmark = {nodes: 0, peakNodes: 0, export: null, transitions: []};
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function(...args) {
      if (!(this.context instanceof OfflineAudioContext)) {
        benchmark.peakNodes = Math.max(benchmark.peakNodes, ++benchmark.nodes);
        this.addEventListener('ended', () => --benchmark.nodes, {once:true});
      }
      return start.apply(this, args);
    };
    const create = URL.createObjectURL;
    URL.createObjectURL = function(blob) {
      if (blob.type === 'audio/wav') benchmark.export = blob;
      return create.call(URL, blob);
    };
    HTMLAnchorElement.prototype.click = function() {};`});
  const navigation = await call('Page.navigate', {url: base + '/jam.html'});
  if (navigation.errorText) throw Error(navigation.errorText);
  await until(() => evaluate("document.querySelector('#jam-backend-status') && !document.querySelector('#jam-opening-mode option[value=cold]').disabled"), 15);
  if (process.env.JAMMATE_BENCH_WAV) {
    const audio = (await readFile(process.env.JAMMATE_BENCH_WAV)).toString('base64');
    report.signal = await evaluate(`(async () => {
      const {scheduleHandoff} = await import('/jam-renderer-audio.js');
      const bytes = Uint8Array.from(atob(${JSON.stringify(audio)}), value => value.charCodeAt(0));
      const wav = new DataView(bytes.buffer);
      const rate = wav.getUint32(24, true), channels = wav.getUint16(22, true);
      const frames = wav.getUint32(40, true) / (2 * channels);
      if (wav.getUint16(34, true) !== 16 || wav.getUint32(36) !== 0x64617461) {
        throw Error('Signal comparison expects a canonical PCM16 WAV');
      }
      const exact = new OfflineAudioContext(channels, frames, rate);
      const source = exact.createBufferSource();
      source.buffer = await exact.decodeAudioData(bytes.slice().buffer);
      source.connect(exact.destination);
      source.start();
      const rendered = await exact.startRendering();
      let maxDelta = 0;
      for (let channel = 0; channel < channels; channel++) {
        const pcm = rendered.getChannelData(channel);
        for (let i = 0; i < frames; i++) {
          maxDelta = Math.max(maxDelta, Math.abs(pcm[i] - wav.getInt16(44 + (i * channels + channel) * 2, true) / 32768));
        }
      }
      async function switchAudit(exclusive) {
        const context = new OfflineAudioContext(2, 48000, 48000);
        const baseline = context.createGain(), preview = context.createGain();
        baseline.connect(context.destination); preview.connect(context.destination);
        for (const [index, gain] of [baseline, preview].entries()) {
          const node = context.createBufferSource();
          node.buffer = context.createBuffer(2, 48000, 48000);
          node.buffer.getChannelData(index).fill(.4);
          node.connect(gain); node.start(index === 0 ? 0 : .25);
          if (index === 1) node.stop(.75);
        }
        if (exclusive) scheduleHandoff(baseline.gain, preview.gain, .25, .75, 0);
        else {
          baseline.gain.setValueAtTime(0, .25);
          baseline.gain.setValueAtTime(1, .75);
        }
        const result = await context.startRendering();
        const left = result.getChannelData(0), right = result.getChannelData(1);
        let overlapFrames = 0, maxStep = 0;
        for (let i = 1; i < left.length; i++) {
          if (Math.abs(left[i]) > 1e-5 && Math.abs(right[i]) > 1e-5) overlapFrames++;
          maxStep = Math.max(maxStep, Math.abs(left[i] - left[i-1]), Math.abs(right[i] - right[i-1]));
        }
        return {overlapFrames, maxStep};
      }
      return {sameRateWavMaxDelta: maxDelta, sampleRate: rate, wavChannels: channels,
        hardSwitch: await switchAudit(false), rampedExclusiveSwitch: await switchAudit(true)};
    })()`);
  }
  if (await evaluate(`document.querySelector('#jam-renderer option[value="${renderer}"]')?.disabled`)) {
    throw Error('The running service does not expose ' + renderer);
  }
  await evaluate(`{
    const choose = (id, value, type = 'change') => {
      const el = document.getElementById(id); el.value = value; el.dispatchEvent(new Event(type, {bubbles:true}));
    };
    choose('jam-renderer', ${JSON.stringify(renderer)});
    choose('jam-opening-mode', 'cold');
    choose('jam-cold-bpm', '180', 'input');
    choose('jam-cold-key', 'C 大调');
    choose('jam-opening-text', 'C 大调，和弦设为 C-Am-F-G，第 1、3 拍。bossa nova, nylon guitar, light percussion', 'input');
    document.getElementById('jam-debug').open = ${debug === 'open'};
    const playback = document.getElementById('jam-playback');
    new MutationObserver(() => {
      const phase = playback.textContent.includes('MRT2 片段') ? 'mrt2_preview' : 'other';
      if (benchmark.transitions.at(-1)?.phase !== phase) benchmark.transitions.push({phase, ms:performance.now()});
    }).observe(playback, {childList:true});
  }`);
  const upload = process.env.JAMMATE_BENCH_UPLOAD;
  if (upload) {
    const encoded = (await readFile(upload)).toString('base64');
    await evaluate(`{
      const input = document.querySelector('[data-jam-input="humming"] input[type=file]');
      const transfer = new DataTransfer();
      const bytes = Uint8Array.from(atob(${JSON.stringify(encoded)}), value => value.charCodeAt(0));
      transfer.items.add(new File([bytes], ${JSON.stringify(basename(upload))},
        {type:${JSON.stringify(upload.endsWith('.m4a') ? 'audio/mp4' : 'audio/wav')}}));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', {bubbles:true}));
    }`);
    await until(() => evaluate(`Boolean(document.querySelector('[data-jam-input="humming"] audio[data-input-audio]:not([hidden])'))`), 15);
  }
  await until(() => evaluate("!document.getElementById('jam-start').disabled"), 10);
  const started = Date.now();
  await evaluate("document.getElementById('jam-start').click()");
  for (const bar of [...new Set([1, 25, 50, 100, +bars].filter(n => n <= +bars))].sort((a, b) => a - b)) {
    await until(() => evaluate(`+document.getElementById('jam-bar').textContent >= ${bar}`), 90);
    const heap = await call('Runtime.getHeapUsage');
    const perf = Object.fromEntries((await call('Performance.getMetrics')).metrics.map(metric => [metric.name, metric.value]));
    const audio = await evaluate(`({nodes:benchmark.nodes, peakNodes:benchmark.peakNodes,
      playback:document.getElementById('jam-playback').textContent,
      mrt2_hint:document.getElementById('jam-mrt2-hint').textContent})`);
    const observation = {bar, elapsed_s: (Date.now() - started) / 1000, heap, task_s: perf.TaskDuration, ...audio};
    if (/^\d+$/.test(process.env.JAMMATE_BENCH_SERVER_PID ?? '')) {
      const [cpu, rss] = execFileSync('ps', ['-p', process.env.JAMMATE_BENCH_SERVER_PID, '-o', 'pcpu=,rss='],
        {encoding: 'utf8'}).trim().split(/\s+/).map(Number);
      observation.backend = {cpu_percent: cpu, rss_bytes: rss * 1024};
    }
    report.observations.push(observation);
    console.log(JSON.stringify(observation));
  }
  if (upload) {
    const {body} = await call('Network.getResponseBody', {requestId: startRequest});
    const session = JSON.parse(body).session;
    report.upload = await evaluate(`fetch('/api/jam/debug', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({session:${JSON.stringify(session)}})
    }).then(response => response.json()).then(value => ({
      recognized: value.opening.input_events.filter(event => event.source === 'humming'),
      renderer: value.renderer, stream: value.mrt2_stream
    }))`);
  }
  await evaluate("benchmark.export = null; document.getElementById('jam-stop').click(); document.getElementById('jam-export').click()");
  report.export_bytes = await until(() => evaluate('benchmark.export?.size'), 120);
  console.log(JSON.stringify({export_bytes: report.export_bytes}));
  if (process.env.JAMMATE_BENCH_SAVE_WAV === '1') {
    const chunks = [];
    for (let offset = 0; offset < report.export_bytes; offset += 262144) {
      const wav = await evaluate(`new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(benchmark.export.slice(${offset}, ${offset + 262144}));
      })`);
      chunks.push(Buffer.from(wav, 'base64'));
    }
    await writeFile(join(out, 'performance.wav'), Buffer.concat(chunks));
  }
  report.transitions = await evaluate('benchmark.transitions');
  report.http = Object.fromEntries(Object.entries(httpTimes).map(([key, values]) => {
    values.sort((a, b) => a - b);
    return [key, {count: values.length, p50_ms: values[Math.floor(values.length / 2)],
      p95_ms: values[Math.floor((values.length - 1) * .95)]}];
  }));
  report.status = report.errors.length ? 'observed_errors' : 'completed';
} catch (error) {
  report.status = 'failed';
  report.errors.push(error.message);
  process.exitCode = 1;
} finally {
  await writeFile(join(out, 'browser.json'), JSON.stringify(report, null, 2) + '\n');
  socket?.close();
  chrome.kill();
}
