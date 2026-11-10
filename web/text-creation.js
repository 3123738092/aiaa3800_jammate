const $ = id => document.getElementById(id);
const prompt = $('creation-prompt'), agent = $('text-agent');
const bpm = $('text-bpm'), profile = $('text-profile');
const audio = $('text-audio'), status = $('text-status');
const example = document.querySelector('[data-prompt]');
let session = crypto.randomUUID();
let turn = 0, pending = null;
const attempts = new Map();
let urls = [];

async function post(route, body, signal) {
  const response = await fetch('/api/reference/text/' + route, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}

function sync() {
  const busy = Boolean(pending);
  for (const field of [prompt, example, $('new-work'), $('text-generate')]) field.disabled = busy;
  for (const field of [agent, bpm, profile]) field.disabled = busy || turn > 0;
  $('text-generate').textContent = turn ? '继续调整' : '生成第一段';
}

function clearAudio() {
  audio.pause();
  audio.removeAttribute('src');
  audio.load();
  for (const url of urls) URL.revokeObjectURL(url);
  urls = [];
  $('text-output').hidden = true;
}

function media(encoded, type) {
  const bytes = Uint8Array.from(atob(encoded), value => value.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], {type}));
  urls.push(url);
  return url;
}

function newWork() {
  session = crypto.randomUUID();
  turn = 0;
  attempts.clear();
  clearAudio();
  status.textContent = '';
  sync();
}

for (const field of [agent, bpm, profile]) field.addEventListener('change', newWork);
$('new-work').addEventListener('click', newWork);
example.onclick = () => {
  prompt.value = example.dataset.prompt;
  prompt.focus();
};

$('text-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (pending || !prompt.value.trim()) return;
  const input = {session_id: session, after_turn: turn, agent: agent.value, bpm: Number(bpm.value),
    profile: profile.value, text: prompt.value.trim()};
  const signature = JSON.stringify(input);
  if (!attempts.has(signature)) {
    attempts.set(signature, {...input, request_id: crypto.randomUUID()});
  }
  const body = attempts.get(signature);
  const request = new AbortController();
  pending = request;
  sync();
  status.textContent = '正在编排和生成…';
  try {
    const result = await post('turn', body, request.signal);
    if (request !== pending) return;
    clearAudio();
    audio.src = media(result.wav, 'audio/wav');
    $('text-wav').href = audio.src;
    $('text-midi').href = media(result.midi, 'audio/midi');
    $('text-output').hidden = false;
    turn = result.turn;
    attempts.clear();
    const mode = result.agent === 'llm' ? '大模型' : '本地规则';
    status.textContent = `第 ${turn} 段 · ${result.bars} 小节 · ${result.tempo_bpm.toFixed(1)} BPM · ${mode}`;
    $('text-decision').textContent = [result.plan.reason, ...result.plan.unresolved].filter(Boolean).join('。');
  } catch (error) {
    if (request === pending) status.textContent = `生成失败：${error.message}。可保留原要求重试。`;
  } finally {
    if (request === pending) {
      pending = null;
      sync();
    }
  }
});

window.addEventListener('pagehide', event => {
  pending?.abort();
  pending = null;
  sync();
  if (event.persisted) audio.pause();
  else clearAudio();
});

post('status', {}).then(result => {
  agent.options[0].disabled = !result.llm_ready;
  for (const option of [...profile.options].slice(1)) option.disabled = !result.soundfont;
  $('provider-state').textContent = result.llm_ready ? '大模型已配置，可在上方选择。' : result.notice;
}).catch(error => {
  $('provider-state').textContent = `无法检查配置：${error.message}`;
});
