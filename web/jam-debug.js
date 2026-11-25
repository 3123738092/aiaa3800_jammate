import {outputRows, pipelineStages, streamForBar} from './jam-pipeline-debug.js?v=pipeline-log';

const colors = ['#71d7a4', '#f2bd73', '#7cb9f2', '#d7a7e9', '#f38f91', '#7bd8d7'];
const pitches = ['C', 'C♯', 'D', 'E♭', 'E', 'F', 'F♯', 'G', 'A♭', 'A', 'B♭', 'B'];
const drumNames = {35: '底鼓', 36: '底鼓', 38: '军鼓', 40: '军鼓', 42: '闭镲', 44: '踏镲',
  46: '开镲', 49: '吊镲', 51: '叮叮镲'};
const statuses = {buffered: '等待下一小节起拍', thinking: '理解指令', arranging: '编排或生成中',
  waiting_ready: '等待生成结果', scheduled: '已排程', applied: '已起拍', held: '保持当前演奏',
  expired: '已超时', superseded: '被新指令替代', cancelled: '已取消', preview: 'MRT2 试听就绪'};

export function initJamDebug(post, label) {
  const field = name => document.getElementById('jam-debug-' + name);
  const panel = document.getElementById('jam-debug');
  const track = field('track'), barSelect = field('bar'), turn = field('turn'), canvas = field('roll');
  let session = null;
  let frame = null;
  let nextRefresh = 0;
  let pending = false;
  let missing = false;
  let browserErrors = [];
  let browserPlayback = null;
  let status = '';
  let trackChoices = '';
  let turnChoices = '';
  let visibleBar = null;
  let trackNames = [];

  function write(name, value) {
    const target = field(name);
    if (target.textContent !== value) target.textContent = value;
  }

  function json(name, value) {
    write(name, value == null ? '—' : JSON.stringify(value, null, 2));
  }

  function changedContent(target, data) {
    const signature = JSON.stringify(data);
    if (target.dataset.signature === signature) return false;
    target.dataset.signature = signature;
    return true;
  }

  function fields(rows) {
    return rows.filter(([, value]) => value != null && value !== '').map(([key, value]) => {
      const row = document.createElement('div');
      const title = document.createElement('dt'), text = document.createElement('dd');
      title.textContent = key;
      text.textContent = value;
      row.append(title, text);
      return row;
    });
  }

  function paintPipeline(selectedTurn) {
    const rows = pipelineStages(frame, selectedTurn);
    const pipeline = field('pipeline');
    if (changedContent(pipeline, rows)) {
      pipeline.replaceChildren(...rows.map(([name, entries]) => {
        const item = document.createElement('li');
        item.dataset.stage = name;
        const title = document.createElement('h3'), description = document.createElement('dl');
        title.textContent = name;
        description.append(...fields(entries.length ? entries : [['状态', '暂无记录']]));
        item.append(title, description);
        return item;
      }));
    }
    const output = field('output');
    const sound = outputRows(frame, visibleBar, browserPlayback);
    if (changedContent(output, sound)) output.replaceChildren(...fields(sound));
  }

  function options(select, defaultLabel, values) {
    const selected = select.value;
    const first = new Option(defaultLabel, '');
    select.replaceChildren(first, ...values.map(([value, text]) => new Option(text, value)));
    select.value = [...select.options].some(option => option.value === selected) ? selected : '';
  }

  function color(name) {
    return colors[Math.max(0, trackNames.indexOf(name)) % colors.length];
  }

  function drawRoll() {
    if (!panel.open) return;
    const context = canvas.getContext('2d');
    const notes = (visibleBar?.notes ?? []).filter(note => !track.value || note.instrument === track.value);
    const drums = [...new Set(notes.filter(note => note.instrument === 'drums').map(note => note.pitch))].sort((a, b) => a - b);
    const pitched = notes.filter(note => note.instrument !== 'drums').map(note => note.pitch);
    const low = Math.min(...pitched), high = Math.max(...pitched);
    const pitchRows = pitched.length ? high - low + 1 : 0;
    const rows = Math.max(1, pitchRows + drums.length);
    const height = Math.max(168, Math.min(358, 40 + rows * 14));
    const width = Math.max(1, canvas.clientWidth);
    const pixelRatio = Math.min(devicePixelRatio || 1, 2);
    canvas.style.height = height + 'px';
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    context.scale(pixelRatio, pixelRatio);
    context.fillStyle = '#11251f';
    context.fillRect(0, 0, width, height);
    if (!notes.length) {
      context.fillStyle = '#b9cbbd';
      context.font = '13px system-ui';
      context.fillText(visibleBar ? '这组声部没有音符' : '暂无小节', 16, 29);
      canvas.setAttribute('aria-label', visibleBar ? '所选声部在此小节没有音符' : '暂无可显示的小节');
      return;
    }
    const left = 68, top = 26, gridWidth = Math.max(1, width - left - 12);
    const rowHeight = (height - top - 9) / rows;
    const beats = visibleBar.beats;
    const yFor = index => top + index * rowHeight;
    context.font = '11px system-ui';
    context.textBaseline = 'middle';
    for (let i = 0; i < rows; i++) {
      const midi = high - i;
      const drum = i >= pitchRows;
      const pitch = drum ? drums[i - pitchRows] : midi;
      const octave = !drum && midi % 12 === 0;
      context.fillStyle = octave || drum ? '#273f34' : i % 2 ? '#1a3027' : '#1d362b';
      context.fillRect(left, yFor(i), gridWidth, rowHeight);
      if (drum || octave || rowHeight >= 12) {
        context.fillStyle = '#d4e2d5';
        const name = drum ? drumNames[pitch] ?? String(pitch) : pitches[midi % 12] + (Math.floor(midi / 12) - 1);
        context.fillText(name, 9, yFor(i) + rowHeight / 2);
      }
    }
    for (let beat = 0; beat <= beats; beat += .25) {
      const x = left + beat / beats * gridWidth;
      const whole = Number.isInteger(beat);
      context.fillStyle = whole ? '#749384' : '#345246';
      context.fillRect(x, top, 1, height - top - 9);
      if (whole && beat < beats) {
        context.fillStyle = '#d4e2d5';
        context.fillText('第 ' + (beat + 1) + ' 拍', x + 4, 13);
      }
    }
    for (const note of notes) {
      const row = note.instrument === 'drums' ? pitchRows + drums.indexOf(note.pitch) : high - note.pitch;
      const x = left + note.start / beats * gridWidth;
      const span = Math.min(note.duration, beats - note.start) / beats * gridWidth;
      context.fillStyle = color(note.instrument);
      context.globalAlpha = .55 + note.velocity / 127 * .45;
      context.fillRect(x, yFor(row) + .6, Math.max(2, span - .8), Math.max(1, rowHeight - 1.2));
    }
    context.globalAlpha = 1;
    if (barSelect.value === 'current') {
      const progress = (frame.sample - visibleBar.start_sample) / (visibleBar.end_sample - visibleBar.start_sample);
      const x = left + Math.min(1, Math.max(0, progress)) * gridWidth;
      context.fillStyle = '#fff1bd';
      context.fillRect(x, top - 2, 2, height - top - 5);
    }
    canvas.setAttribute('aria-label', '第 ' + (visibleBar.bar_index + 1) + ' 小节，' + notes.length + ' 个音符');
  }

  function paint() {
    if (!panel.open) return;
    const state = frame?.jam_state;
    const selectedTurn = turn.value === 'opening' ? null :
      frame?.turns.find(item => item.input_event.event_id === turn.value) ?? frame?.turns[0];
    const engine = {mrt2: '实时伴奏 / MRT2 独立试听', fluidsynth: 'FluidSynth',
      mrt2_direct: 'MRT2 独立生成', mrt2_stream: 'MRT2 音符条件流式'}[frame?.renderer] ?? '内置合成器';
    write('summary', frame ? 'v' + state.state_version + ' · ' + engine : '等待开始');
    write('bpm', frame ? frame.bpm + ' BPM' : '—');
    const key = frame?.key?.replace(' major', ' 大调').replace(' minor', ' 小调');
    write('key', key ?? '—');
    write('chord', frame?.chord ?? '—');
    write('progression', frame?.progression?.join(' → ') || '—');
    write('status', status || (frame ? '保留最近 12 轮。小节从 1 起显示，原始 JSON 从 0 起。' : '开始演奏后记录输入、模型和排程。'));

    visibleBar = frame?.[barSelect.value] ?? null;
    paintPipeline(selectedTurn);
    if (frame?.renderer === 'mrt2' && visibleBar) {
      write('score-status', '第 ' + (visibleBar.bar_index + 1) + ' 小节 · 实时伴奏音符。MRT2 试听条件见「模型、回复与错误」。');
    } else if (frame?.renderer === 'mrt2_direct' && visibleBar) {
      write('score-status', '第 ' + (visibleBar.bar_index + 1) + ' 小节 · BandPlan 文本条件见「模型、回复与错误」。');
    } else {
      write('score-status', visibleBar ? '第 ' + (visibleBar.bar_index + 1) + ' 小节 · ' +
        visibleBar.tempo_bpm + ' BPM · ' + visibleBar.notes.length + ' 个音符' :
        barSelect.value === 'queued' ? '还没有排入播放缓冲区的小节。' : '等待浏览器确认起拍。');
    }
    const layers = state?.arrangement?.layers ?? [];
    trackNames = layers.map(layer => layer.name);
    const nextTracks = layers.map(layer => [layer.name, label(layer.name, frame?.timbres)]);
    if (JSON.stringify(nextTracks) !== trackChoices) {
      trackChoices = JSON.stringify(nextTracks);
      options(track, '全部', nextTracks);
    }
    const body = field('tracks');
    const trackRows = layers.map(layer => {
      const part = visibleBar?.notes.filter(note => note.instrument === layer.name);
      const onsets = part ? new Set(part.map(note => note.start)).size : null;
      return [label(layer.name, frame?.timbres), layer.active ? Math.round(layer.density * 100) + '%' : '静音',
        Math.round(layer.level * 100) + '%', onsets ?? '—', part?.length ?? '—',
        onsets === null ? '—' : (onsets * visibleBar.tempo_bpm / (60 * visibleBar.beats)).toFixed(2)];
    });
    const tableSignature = JSON.stringify(trackRows);
    if (body.dataset.signature !== tableSignature) {
      body.dataset.signature = tableSignature;
      body.replaceChildren(...trackRows.map(([name, ...values], index) => {
        const row = document.createElement('tr');
        const title = document.createElement('th');
        title.scope = 'row';
        const swatch = document.createElement('span');
        swatch.className = 'jam-debug-swatch';
        swatch.style.backgroundColor = color(layers[index].name);
        title.append(swatch, name);
        row.append(title, ...values.map(value => {
          const cell = document.createElement('td');
          cell.textContent = value;
          return cell;
        }));
        return row;
      }));
    }
    drawRoll();

    const nextTurns = frame?.turns.map(item => [item.input_event.event_id, item.feedback.text.slice(0, 54)]) ?? [];
    if (frame?.opening) nextTurns.unshift(['opening', '开场']);
    if (JSON.stringify(nextTurns) !== turnChoices) {
      turnChoices = JSON.stringify(nextTurns);
      options(turn, '最新输入', nextTurns);
    }
    const reply = selectedTurn?.feedback;
    const activeStatus = selectedTurn?.plan_status === 'cancelled' ? 'cancelled' : reply?.status;
    write('turn-status', reply ? [statuses[activeStatus] ?? activeStatus,
      reply.bar != null ? '第 ' + (reply.bar + 1) + ' 小节' : '',
      reply.message, selectedTurn.elapsed_ms != null ? Math.round(selectedTurn.elapsed_ms) + ' ms' : ''].filter(Boolean).join(' · ') :
      frame?.opening ? '当前查看开场。' : '尚未收到指令。');
    json('opening', frame?.opening);
    json('event', selectedTurn?.input_event ?? frame?.opening?.input_events);
    json('state', state ? {current: state, at_decision: selectedTurn?.decision_state,
      after_playback: selectedTurn?.state_after_playback} : null);
    json('plan', selectedTurn ? {proposed: selectedTurn.proposed_plan, bound: selectedTurn.bound_plan,
      status: selectedTurn.plan_status ?? reply.status, slot: selectedTurn.slot, playback_started: selectedTurn.playback_receipt} :
      frame?.opening && {bound: frame.opening.mrt2_plan ?? frame.opening.solo_plan ?? frame.opening.melody_replay_plan,
        playback_started: frame.opening.playback_receipt});
    const stream = streamForBar(frame, visibleBar);
    json('models', selectedTurn || stream ? {decision: selectedTurn?.decision, arrangement: selectedTurn?.arrangement,
      inference: selectedTurn?.inference, mrt2_conditions: selectedTurn?.conditioning, stream,
      error: selectedTurn?.error} : null);
    json('capture', frame || browserErrors.length ? {capture: frame?.captures, browser: browserErrors} : null);
    json('notes', visibleBar);
    field('copy').disabled = !frame && !browserErrors.length;
  }

  async function refresh() {
    if (!panel.open || !session || pending || missing || performance.now() < nextRefresh) return;
    const active = session;
    pending = true;
    nextRefresh = performance.now() + 700;
    try {
      const snapshot = await post('debug', {session: active}, 2200);
      if (session !== active) return;
      frame = snapshot;
      status = '';
    } catch (error) {
      if (session !== active) return;
      missing = error.message === 'unknown Jam endpoint';
      status = missing ? '当前服务需要重启后才能提供调试数据。' :
        '暂时无法刷新调试数据 · ' + error.message;
    } finally {
      if (session === active) {
        pending = false;
        paint();
      }
    }
  }

  panel.addEventListener('toggle', () => {
    if (!panel.open) return;
    paint();
    refresh();
  });
  field('open').addEventListener('click', () => {
    panel.open = true;
    panel.scrollIntoView({block: 'start'});
    panel.querySelector('summary').focus({preventScroll: true});
  });
  for (const control of [track, barSelect, turn]) control.addEventListener('change', paint);
  new ResizeObserver(drawRoll).observe(canvas.parentElement);
  field('copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify({...frame, browser_playback: browserPlayback, browser_errors: browserErrors}, null, 2));
      write('status', '调试 JSON 已复制。');
    } catch {
      write('status', '无法直接复制。你可以展开下方 JSON 并手动复制。');
    }
  });
  return {
    start(id) {
      session = id;
      frame = null;
      pending = missing = false;
      nextRefresh = 0;
      browserErrors = [];
      browserPlayback = null;
      status = '';
      turn.value = track.value = '';
      barSelect.value = 'current';
      paint();
      refresh();
    },
    refresh,
    setPlayback(playback) {
      browserPlayback = playback;
    },
    stop() {
      session = null;
      if (browserPlayback) browserPlayback = {...browserPlayback, source: '已停止'};
      status = frame ? '会话已结束，保留最后一次调试快照。' : '';
      paint();
    },
    error(stage, error) {
      const message = error.message || error.name || String(error);
      if (browserErrors[0]?.stage === stage && browserErrors[0].error === message) return;
      browserErrors.unshift({stage, error: message});
      browserErrors = browserErrors.slice(0, 12);
      paint();
    },
  };
}
