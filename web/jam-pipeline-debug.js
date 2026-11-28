const engines = {synth: '内置合成器', fluidsynth: 'FluidSynth / SoundFont',
  mrt2: '实时伴奏 + MRT2 试听', mrt2_direct: 'MRT2 独立生成', mrt2_stream: 'MRT2 音符条件流式'};
const sources = {text: '文字', speech: '语音转录', rhythm: '节奏', humming: '哼唱'};
const gridTypes = {text: '文字风格 token', audio_style: '音频风格 token',
  text_pitch_drum: '风格 token + 音高 / 鼓网格', silence: '静音 PCM'};
const routes = {llm: '大模型 Agent', rule: '本地规则', local_explicit: '本地规则', explicit_music_request: '明确的音乐指令',
  jev_direct: 'JEV 路由', mrt2_style: 'MRT2 风格意图', mrt2_audio_reference: 'MRT2 录音参考',
  cold_audio: '按录音素材编排', request_gate: '请求校验'};

const barName = index => Number.isInteger(index) ? `第 ${index + 1} 小节` : null;
const joined = (items, sep = ' · ') => items?.length ? items.join(sep) : null;
const value = (item, suffix) => item == null ? null : `${item}${suffix}`;

function eventContent(event) {
  const payload = event.payload;
  if (payload.text != null) return payload.text;
  if (payload.rhythm) return `${payload.rhythm.tempo_bpm} BPM · 起音拍 ${joined(payload.rhythm.onset_beats.map(beat => beat + 1), '、') ?? '无'}`;
  const melody = payload.melody;
  if (melody) return `${melody.source.durationSec.toFixed(2)} 秒 · ${melody.notes.length} 个音符` +
    (event.provenance?.audio_ref ? ' · 保存音频参考' : '');
  return payload.command ?? '—';
}

export function streamForBar(frame, bar) {
  return frame?.mrt2_stream?.recent_bars.find(item => item.bar === bar?.bar_index);
}

export function outputRows(frame, bar, browserPlayback) {
  const output = bar?.output;
  const stream = streamForBar(frame, bar);
  return [
    ['浏览器当前声音', browserPlayback?.source],
    ['试听片段 ID', browserPlayback?.preview_id],
    ['流式缓冲重接', browserPlayback?.buffer_reconnections ? value(browserPlayback.buffer_reconnections, ' 次') : null],
    ['小节音频', output && [engines[output.renderer], output.model, output.format,
      `${output.sample_rate} Hz / ${output.channels} 声道`, `${(output.sample_frames / output.sample_rate).toFixed(2)} 秒`].filter(Boolean).join(' · ')],
    ['实际音色 / 声部', output?.tracks && (joined(output.tracks.map(track => [
      track.score_part === track.instrument ? track.instrument : `${track.score_part} → ${track.instrument}`,
      track.program == null ? (track.engine === 'fluidsynth' ? 'SoundFont 鼓组' : '内置合成') :
        `${track.timbre ?? 'SoundFont'} (GM ${track.program})`, `${track.notes} 音符`,
    ].join(' · ')), '\n') ?? '静音')],
    ['MRT2 条件', stream ? gridTypes[stream.conditioning] : output?.model ? '等待该小节的生成回执' : null],
    ['送模 Prompt', stream?.style_prompt],
    ['吉他谐波增强上限', value(stream?.guitar_focus_db, ' dB')],
    ['录音参考', stream?.audio_ref_event],
    ['音高 / 鼓网格', stream?.grid && `${stream.grid.frame_rate} Hz · ${stream.grid.pitch_cells} 个有效音高格 · ${stream.grid.drum_frames} 个有鼓帧`],
    ['首个模型音频帧', value(stream?.first_model_pcm_ms, ' ms')],
  ];
}

export function pipelineStages(frame, turn) {
  if (!frame) return [];
  const opening = turn ? null : frame.opening;
  const events = turn ? [turn.input_event] : opening?.input_events ?? [];
  const state = turn ? turn.decision_state : opening?.settings;
  const trace = turn?.decision;
  const proposed = turn?.proposed_plan;
  const plan = turn?.bound_plan ?? proposed ?? opening?.mrt2_plan ?? opening?.solo_plan ?? opening?.melody_replay_plan;
  const receipt = turn?.playback_receipt ?? opening?.playback_receipt;
  const feedback = turn?.feedback;
  const stream = plan && frame.mrt2_stream?.recent_bars.find(item => item.plan_id === plan.plan_id);
  const conditioning = turn?.conditioning ?? opening?.mrt2_preview?.conditioning;
  const preview = turn?.inference ?? opening?.mrt2_preview?.inference;
  const input = turn?.renderer_input;
  const engine = turn ? routes[trace?.source] ?? trace?.source ?? (proposed ? '本地规则' : null) : opening?.model ?? '本地开场规则';
  return [
    ['输入', events.map(event => [sources[event.source] ?? event.source, eventContent(event)])],
    ['InputEvent', events.map(event => [event.event_type, `${event.event_id} · ${event.provenance?.producer ?? event.source}`])],
    ['JamState 与决策', [['状态', state ? `v${state.state_version ?? plan?.based_on_state_version ?? 0} · ${state.transport?.tempo_bpm ?? state.tempo_bpm} BPM` : null],
      ['决策来源', engine], ['模型', trace?.attempts?.at(-1)?.model ?? trace?.requested_model],
      ['本轮耗时', value(turn?.elapsed_ms, ' ms')], ['依据', plan?.reason ?? opening?.reason]]],
    ['BandPlan', plan ? [
      ['计划', `${plan.strategy} · ${plan.plan_id}`],
      ['动作', joined(plan.actions.map(action => `${action.action} / ${action.scope}` +
        (Object.keys(action.parameters).length ? ` ${JSON.stringify(action.parameters)}` : '')), '\n') ?? '无显式动作'],
      ['保留', joined(plan.preserve, '、')], ['风格', plan.style],
      ['控制', Object.keys(plan.transport).length ? JSON.stringify(plan.transport) : null],
      ['提议 / 排程', joined([proposed && `提议 ${barName(proposed.effective_at.bar)}`,
        turn?.slot && `排程 ${barName(turn.slot.bar)}`].filter(Boolean))],
      ['有效期', `${plan.valid_for_bars} 小节`], ['未解决', joined(plan.unresolved, '\n')],
    ] : [['状态', opening ? '开场设置直接写入 JamState' : '等待决策']]],
    ['Renderer 转换', [['路径', engines[frame.renderer]],
      ['编排', input?.kind === 'score' ? `${input.arrangement === 'llm' ? 'LLM' : '本地规则'} · 准备 ${input.prepare_bars} 小节 / ${input.prepared_notes} 音符` : null],
      ['模型条件', stream ? gridTypes[stream.conditioning] : input?.kind === 'conditioning' ? '等待实际送模回执' : null],
      ['实际 Prompt', stream?.style_prompt ?? (conditioning?.style || preview?.prompt)],
      ['吉他谐波增强上限', value(stream?.guitar_focus_db, ' dB')],
      ['录音参考', stream?.audio_ref_event], ['风格 token', joined(stream?.style_tokens, ', ')],
      ['试听条件', conditioning && `${conditioning.pitch_spans?.length ?? 0} 段音高 · ${conditioning.drum_spans?.length ?? 0} 段鼓点`],
      ['试听推理', preview && [preview.model, preview.backend, preview.status].filter(Boolean).join(' · ')]]],
    ['播放与响应', [['结果', feedback?.message ?? opening?.reason],
      ['播放回执', receipt ? `${barName(receipt.bar)} · 起拍样本 ${receipt.start_sample}` :
        (turn?.slot ? '等待浏览器确认起拍' : null)], ['错误', (turn?.error ?? turn?.preview_error)?.message]]],
  ];
}
