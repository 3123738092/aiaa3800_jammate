// Presentation layer: existing controls and backend modules remain the source of truth.
const $ = id => document.getElementById(id);
const original = document.querySelector('.jam-workspace');
const shell = document.createElement('main');
shell.className = 'studio';
shell.innerHTML = `
  <header class="studio-header"><a class="wordmark" href="/" aria-label="JamMate 首页"><span class="brand-mark">j<span></span></span>JamMate<span class="brand-period">®</span></a><nav><button id="studio-options" aria-expanded="false" aria-controls="studio-drawer">设备设置 <span>↗</span></button><button id="studio-inspect" aria-expanded="false" aria-controls="jam-debug">⌘ 日志</button></nav></header>
  <div class="studio-intro"><div><h1>留一点时间，<br>一起 Jam。</h1><p>哼一句，弹几个音，或告诉乐队你的想法。</p></div><span class="session-label"><i></i> YOUR LITTLE LISTENING ROOM</span></div>
  <section class="machine" aria-label="JamMate 桌面音乐设备">
    <span class="screw screw-tl"></span><span class="screw screw-tr"></span><span class="screw screw-bl"></span><span class="screw screw-br"></span>
    <div class="machine-topline"><span>JamMate <b> / </b> SESSION INSTRUMENT</span><span>STEREO · JM–01</span></div>
    <div class="machine-upper">
      <section class="rhythm-module"><div class="module-title"><button class="target-select" data-target="drums" aria-pressed="false">节奏组 <span>↗</span></button><span>RHYTHM</span></div><div class="drum-pads" aria-label="鼓垫 · 本地试听"><button data-drum="kick" aria-label="底鼓试听"><span>kick</span><small>01</small></button><button data-drum="snare" aria-label="军鼓试听"><span>snare</span><small>02</small></button><button data-drum="hat" aria-label="踩镲试听"><span>hat</span><small>03</small></button><button data-drum="rim" aria-label="边击试听"><span>rim</span><small>04</small></button></div><div class="module-foot"><span>轻触试听</span><button id="studio-rhythm">录入节奏 ↗</button></div></section>
      <section class="display-module" aria-label="播放屏"><div class="screen"><div class="screen-top"><span><i id="studio-led"></i> <span id="studio-state">READY TO JAM</span></span><span>STEREO</span></div><canvas id="studio-scope" width="960" height="200" aria-label="实际输出音频波形"></canvas><div id="studio-readout"></div><div class="screen-bottom"><span id="studio-key">等待你的第一句旋律</span><span>● ●</span></div></div><div class="transport-row"><div id="studio-transport"></div><div class="beat-lights" aria-label="小节播放指示"><i></i><i></i><i></i><i></i></div><span class="transport-caption">PLAY / STOP</span></div></section>
      <section class="monitor-module"><div class="speaker" aria-hidden="true"><div></div></div><label class="audition-volume">试听音量 <input id="studio-volume" aria-label="本地琴键和鼓垫试听音量" type="range" min="0" max="100" value="55"><output id="studio-volume-value">55</output></label><span class="speaker-caption">NEARFIELD MONITOR</span></section>
    </div>
    <div class="keyboard-module"><div class="keyboard-side"><button class="target-select" data-target="keys" aria-pressed="false">旋律组 ↗</button><span>CHROMATIC<br>KEYBOARD</span><button id="studio-hum">哼唱录入 ↗</button></div><div class="piano" aria-label="键盘 · 本地试听"></div></div>
    <div class="machine-bottomline"><span>DESIGNED FOR PLAYING TOGETHER.</span><span>● <span id="studio-local-state">琴键 / 鼓垫可本地试听</span></span></div>
  </section>
  <form class="studio-command" id="studio-command"><button type="button" id="studio-mic" aria-label="麦克风输入" aria-pressed="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 11v2a6 6 0 0 0 12 0v-2M12 19v3m-4 0h8"/></svg></button><div class="command-body"><button type="button" id="studio-target">全乐队 <span>⌄</span></button><input id="studio-words" aria-label="给乐队的指令" placeholder="来一段慵懒的爵士，鼓轻一点…" maxlength="480" autocomplete="off"></div><button id="studio-send" type="submit" aria-label="发送给乐队">↵</button></form>
  <div class="studio-feedback"><span id="studio-message" role="status">输入一句话，再按播放。也可以先试试琴键和鼓垫。</span><button id="studio-save" disabled>导出录音 ↓</button></div>
  <div class="studio-review"></div><div class="studio-layers"></div>
  <details id="studio-drawer"><summary>输入素材与设备设置</summary><div id="studio-legacy"></div></details>
  <footer class="studio-footer"><span>少一点操作，多一点音乐。</span><span>JamMate — play it your way.</span></footer>`;
original.before(shell);
$('studio-legacy').append(original);
original.querySelector('nav').hidden = true;
original.querySelector('h1').hidden = true;
const move = (selector, target) => { const node = original.querySelector(selector); if (node) $(target).append(node); };
move('.jam-readout', 'studio-readout');
move('.jam-buttons', 'studio-transport');
shell.querySelector('.studio-layers').append($('jam-layers'));
shell.querySelector('.studio-review').append($('opening-mic-review'));
shell.append($('jam-debug'));
$('jam-start').title = '根据开场素材开始 Jam';
$('jam-stop').title = '结束当前会话';
const drawer = $('studio-drawer');
function reveal(selector) {
  drawer.open = true;
  $('studio-options').setAttribute('aria-expanded', 'true');
  const node = document.querySelector(selector);
  node.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center'});
  node.querySelector('button,textarea,input')?.focus({preventScroll: true});
}
$('studio-options').onclick = () => { drawer.open = !drawer.open; };
drawer.addEventListener('toggle', () => $('studio-options').setAttribute('aria-expanded', String(drawer.open)));
$('studio-inspect').onclick = () => { $('jam-debug').open = !$('jam-debug').open; if ($('jam-debug').open) $('jam-debug').scrollIntoView({block:'start',behavior:'smooth'}); };
$('jam-debug').addEventListener('toggle', () => $('studio-inspect').setAttribute('aria-expanded', String($('jam-debug').open)));
if (new URLSearchParams(location.search).get('release') === '1') { $('jam-debug').hidden = true; $('studio-inspect').hidden = true; $('jam-debug-open').hidden = true; }
$('studio-rhythm').onclick = () => reveal('[data-jam-input="rhythm"]');
$('studio-hum').onclick = () => reveal('[data-jam-input="humming"]');
let target = 'all';
const targetNames = {all:'全乐队',drums:'节奏组',keys:'旋律组'};
function selectTarget(next) {
  target = next;
  $('studio-target').innerHTML = targetNames[next] + ' <span>⌄</span>';
  document.querySelectorAll('.target-select').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.target === next)));
  $('studio-words').focus({preventScroll:true});
}
document.querySelectorAll('[data-target]').forEach(button => button.onclick = () => selectTarget(button.dataset.target));
$('studio-target').onclick = () => selectTarget(({all:'drums',drums:'keys',keys:'all'})[target]);
const words = $('studio-words');
function syncWords() {
  const value = words.value.trim();
  const mentionsInstrument = /鼓|键盘|钢琴|贝斯|主奏|drum|piano|keys|bass/i.test(value);
  const prefixed = value && target !== 'all' && !mentionsInstrument
    ? (target === 'drums' ? '鼓' : '键盘') + value : value;
  const input = $('jam-stop').disabled ? $('jam-opening-text') : $('jam-text');
  input.value = prefixed;
  input.dispatchEvent(new Event('input', {bubbles:true}));
}
words.addEventListener('input', syncWords);
$('studio-command').onsubmit = event => {
  event.preventDefault(); syncWords();
  if (!words.value.trim()) { words.focus(); return; }
  if ($('jam-stop').disabled) {
    if (!$('jam-start').disabled) $('jam-start').click();
    else $('studio-message').textContent = '服务尚未就绪，请展开设备设置查看连接状态。';
  } else if (!$('jam-send').disabled) $('jam-feedback').requestSubmit();
};
$('studio-mic').onclick = () => {
  const mic = $('jam-stop').disabled ? $('opening-mic') : $('jam-microphone');
  if (!mic.disabled) mic.click();
  else $('studio-message').textContent = '麦克风暂不可用，请检查设备设置，或使用文字输入。';
};
$('studio-save').onclick = () => $('jam-export').click();
// Mirror existing statuses rather than inventing backend progress.
const observed = ['jam-start','jam-stop','jam-playback','jam-feedback-status','jam-opening-status','opening-mic-status','jam-voice-status','jam-export','jam-export-status','opening-mic','jam-microphone'];
const observer = new MutationObserver(records => {
  $('studio-save').disabled = $('jam-export').disabled;
  const mic = $('jam-stop').disabled ? $('opening-mic') : $('jam-microphone');
  $('studio-mic').setAttribute('aria-pressed', mic.getAttribute('aria-pressed') || 'false');
  const statusRecord = [...records].reverse().find(r => /status$/.test((r.target.nodeType === 3 ? r.target.parentElement : r.target).id));
  if (statusRecord) {
    const el = statusRecord.target.nodeType === 3 ? statusRecord.target.parentElement : statusRecord.target;
    if (el.textContent.trim()) $('studio-message').textContent = el.textContent;
  }
  const changedStatus = statusRecord && (statusRecord.target.nodeType === 3 ? statusRecord.target.parentElement : statusRecord.target);
  if (changedStatus?.id === 'opening-mic-status' && $('jam-opening-text').value && !words.matches(':focus') && $('jam-stop').disabled) words.value = $('jam-opening-text').value;
  const playing = $('jam-playback').textContent.startsWith('正在演奏');
  shell.classList.toggle('is-playing', playing);
  if (!playing) shell.querySelectorAll('.beat-lights i').forEach(light => light.classList.remove('lit'));
  $('studio-state').textContent = playing ? 'SESSION IN PROGRESS' : !$('jam-stop').disabled ? 'PREPARING SESSION' : 'READY TO JAM';
});
observed.forEach(id => observer.observe($(id), {attributes:true, childList:true, characterData:true, subtree:true}));
let audio, volume, analyser, buffer;
function audition() {
  audio ??= new AudioContext();
  if (!volume) { volume = audio.createGain(); volume.gain.value = Number($('studio-volume').value)/100*.25; volume.connect(audio.destination); }
  audio.resume().catch(() => {});
  return audio;
}
$('studio-volume').oninput = e => { $('studio-volume-value').value = e.target.value; if(volume) volume.gain.value = Number(e.target.value)/100*.25; };
function note(midi, activeButton, velocity=1) {
  const ctx = audition(), now = ctx.currentTime;
  const oscillator = ctx.createOscillator(), envelope = ctx.createGain();
  oscillator.type = 'triangle'; oscillator.frequency.value = 440 * 2 ** ((midi-69)/12);
  envelope.gain.setValueAtTime(0,now); envelope.gain.linearRampToValueAtTime(.7*velocity,now+.008); envelope.gain.exponentialRampToValueAtTime(.001,now+.8);
  oscillator.connect(envelope).connect(volume); oscillator.start(); oscillator.stop(now+.85);
  activeButton?.classList.add('held'); setTimeout(() => activeButton?.classList.remove('held'),180);
  $('studio-local-state').textContent = '本地试听 · ' + ['C','C♯','D','E♭','E','F','F♯','G','A♭','A','B♭','B'][midi%12];
}
const piano = shell.querySelector('.piano');
window.addEventListener('jammate:midi-preview',({detail})=>{if(Number.isInteger(detail.note)&&detail.note>=0&&detail.note<=127)note(detail.note,null,Math.max(.01,Math.min(1,detail.velocity/127)));});
let whiteIndex = 0;
for (let n=48;n<=72;n++) {
  const black = [1,3,6,8,10].includes(n%12), button = document.createElement('button');
  button.className = black ? 'piano-key black' : 'piano-key white';
  button.setAttribute('aria-label', '试听 MIDI 音符 ' + n);
  if (black) button.style.left = `calc(${whiteIndex} * 100% / 15 - 2%)`;
  else { whiteIndex++; if (n%12 === 0) button.innerHTML = `<span>C${Math.floor(n/12)-1}</span>`; }
  button.onpointerdown = () => note(n,button);
  button.onclick = e => { if (e.detail === 0) note(n,button); };
  piano.append(button);
}
document.querySelectorAll('[data-drum]').forEach(button => button.onclick = () => {
  const ctx = audition(), now = ctx.currentTime, kind = button.dataset.drum;
  const envelope = ctx.createGain(); envelope.connect(volume);
  envelope.gain.setValueAtTime(.8,now); envelope.gain.exponentialRampToValueAtTime(.001,now+.22);
  if(kind === 'kick' || kind === 'rim') {
    const oscillator = ctx.createOscillator(); oscillator.frequency.setValueAtTime(kind === 'kick' ? 140 : 650,now); oscillator.frequency.exponentialRampToValueAtTime(kind === 'kick' ? 40 : 350,now+.12); oscillator.connect(envelope); oscillator.start(); oscillator.stop(now+.23);
  } else {
    const noise = ctx.createBuffer(1,ctx.sampleRate*.2,ctx.sampleRate), data = noise.getChannelData(0);
    for(let i=0;i<data.length;i++) data[i] = Math.random()*2-1;
    const source = ctx.createBufferSource(), filter = ctx.createBiquadFilter(); source.buffer=noise; filter.type='highpass'; filter.frequency.value = kind==='hat'?7500:1000; source.connect(filter).connect(envelope); source.start();
  }
  $('studio-local-state').textContent = '本地试听 · ' + kind;
});
window.addEventListener('jammate:audio', ({detail}) => { analyser = detail.context.createAnalyser(); analyser.fftSize = 1024; detail.output.connect(analyser); detail.autoOutput?.connect(analyser); buffer = new Uint8Array(analyser.fftSize); });
window.addEventListener('jammate:view', ({detail}) => {
  shell.querySelectorAll('.beat-lights i').forEach((light,i) => light.classList.toggle('lit', detail.playing && detail.current_bar != null && detail.current_bar%4 === i));
});
const canvas = $('studio-scope'), pen = canvas.getContext('2d');
let lastDraw = 0;
function draw(now) {
  requestAnimationFrame(draw);
  if(now-lastDraw < (matchMedia('(prefers-reduced-motion: reduce)').matches ? 250 : 40)) return;
  lastDraw=now; pen.clearRect(0,0,960,200);
  pen.strokeStyle='#39343f'; pen.lineWidth=1;
  for(let x=0;x<960;x+=48){pen.beginPath();pen.moveTo(x,0);pen.lineTo(x,200);pen.stroke();}
  for(let y=0;y<=200;y+=50){pen.beginPath();pen.moveTo(0,y);pen.lineTo(960,y);pen.stroke();}
  const live=shell.classList.contains('is-playing');
  if(live && analyser) analyser.getByteTimeDomainData(buffer);
  pen.beginPath(); pen.strokeStyle='#cdb8df'; pen.lineWidth=2;
  for(let x=0;x<960;x++) { const y=live && buffer ? 100+(buffer[Math.floor(x/960*buffer.length)]-128)*.8 :100; if(x===0)pen.moveTo(x,y);else pen.lineTo(x,y); }
  pen.stroke();
}
requestAnimationFrame(draw);
