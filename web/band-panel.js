// One render belongs to the selected input. Discard late responses after a new take.
export function attachBandPanel(panel) {
  panel.innerHTML = `<h3>听创作结果</h3>
    <p>从这次输入发展出一段新音乐，生成后可以试听或下载。</p>
    <button type="button" data-render>生成音乐</button>
    <a data-event download="band-input.json">下载创作输入 JSON</a>
    <p role="status" data-status></p>
    <div data-output hidden><audio controls aria-label="创作结果"></audio>
      <p><a data-wav download="band.wav">下载 WAV</a> ·
      <a data-midi download="band.mid">下载 MIDI</a></p></div>`;
  const find = selector => panel.querySelector(selector);
  const button = find('[data-render]'), audio = find('audio');
  const original = document.getElementById('original');
  if (original) {
    audio.addEventListener('play', () => original.pause());
    original.addEventListener('play', () => audio.pause());
  }
  let current, pending;
  const urls = [];

  function url(blob) {
    const value = URL.createObjectURL(blob);
    urls.push(value);
    return value;
  }
  function media(encoded, type) {
    const bytes = Uint8Array.from(atob(encoded), ch => ch.charCodeAt(0));
    return url(new Blob([bytes], {type}));
  }
  function select(event) {
    pending?.abort();
    pending = null;
    current = event;
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    for (const link of panel.querySelectorAll('a')) link.removeAttribute('href');
    for (const old of urls.splice(0)) URL.revokeObjectURL(old);
    find('[data-output]').hidden = true;
    find('[data-status]').textContent = '';
    panel.hidden = !event;
    button.disabled = !event;
    if (event) find('[data-event]').href = url(new Blob([JSON.stringify(event, null, 2)], {type:'application/json'}));
  }
  button.onclick = async () => {
    const event = current;
    if (!event || button.disabled) return;
    const request = new AbortController();
    pending = request;
    button.disabled = true;
    find('[data-status]').textContent = '乐队正在编排和渲染…';
    try {
      const response = await fetch('/api/create', {method:'POST', signal:request.signal,
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(event)});
      const result = await response.json();
      if (request !== pending) return;
      if (!response.ok) throw new Error(result.error);
      const wav = media(result.wav, 'audio/wav');
      audio.src = wav;
      find('[data-wav]').href = wav;
      find('[data-midi]').href = media(result.midi, 'audio/midi');
      find('[data-output]').hidden = false;
      find('[data-status]').textContent = `${result.bars} 小节 · ${result.tempo_bpm.toFixed(1)} BPM · 本地规则 Agent · ${result.renderer}`;
      if (result.warnings.length || result.manager_diagnostics.last_log_error)
        find('[data-status]').textContent += ' · 部分诊断记录未能保存';
    } catch (error) {
      if (request === pending) {
        find('[data-status]').textContent = `生成失败：${error.message}`;
        button.disabled = false;
      }
    } finally {
      if (request === pending) pending = null;
    }
  };
  window.addEventListener('pagehide', event => {
    if (pending) {
      pending.abort();
      pending = null;
      button.disabled = !current;
      find('[data-status]').textContent = '';
    }
    audio.pause();
    if (!event.persisted) select(null);
  });
  select(null);
  return select;
}
