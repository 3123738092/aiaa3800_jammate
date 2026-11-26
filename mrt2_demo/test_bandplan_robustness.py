"""BandPlan 输入空间 × MRT2 输出稳定性测试。

两层：

1. Adapter 层（默认运行，无需模型，秒级）：
   把覆盖代表性的 strategy / action / scope / 参数组合的合法 BandPlan 逐个翻译成
   MRT2 的 25 Hz 条件 token（pianoroll 128 位 + drums 1 位），验证：
   - 每种合法输入都被接受（构造不抛异常、任意时刻输出形状与取值合法）；
   - 时间演化可复现（同输入重放 → 逐帧完全一致）；
   - 计划窗口外干净归零、窗口边界精确生效；
   - 非法输入被明确拒绝（ValueError），而不是静默产出坏 token。

2. 模型层（集成，设 MRT2_INTEGRATION=1 才运行，加载 mrt2_small 约需几十秒）：
   把 adapter 的条件真正喂给官方 MRT2 mlxfn，验证：
   - 全部 BandPlan 都被真实模型接受（不抛异常，PCM 形状 (1920,2)、有限、有界）；
   - 同 seed 同输入 → 输出可复现；
   - 连续帧（1 s 流式）不爆炸：无 NaN、不削波、RMS 有界、不产生死帧；
   - 不同 BandPlan 确实改变输出（条件真实生效），且全部保持在界内。

注意：条件字典的 key 必须用 TokensConfig 的 key（style 是 'mulan_tokens_25hz'，
不是 CFG scale 名 'musiccoca'）；未知 key 会被模型静默丢弃。

运行：
  python -m pytest mrt2_demo/test_bandplan_robustness.py -q
  MRT2_INTEGRATION=1 python -m pytest mrt2_demo/test_bandplan_robustness.py -q
"""
import os
import time

import numpy as np
import pytest

from jammate.contracts import BandPlan
from jammate.contracts.adapters import from_text
from jammate.music import Note
from jammate.orchestration.plans import make_plan

from .adapter import MRT2Adapter
from .fixtures import DEMO_MOTIF, EVENT_ID, MOTIF_ID, demo_state

KEY = 'pianoroll_with_onsets_tokens'
DKEY = 'drum_pianoroll_tokens'

def action(name, scope='band', strength=.7, parameters=None):
    if parameters is None:
        parameters = {}
        if name in ('ECHO_MOTIF', 'VARY_MOTIF'):
            parameters = {'motif_id': MOTIF_ID}
        elif name == 'INTRODUCE_MOTIF':
            parameters = {'motif_id': 'new-motif'}
    return dict(action=name, scope=scope, strength=strength, parameters=parameters)


def make_case(plan_id, plan_kwargs=None, state_kwargs=None):
    options = dict(plan_kwargs or {})
    settings = dict(state_kwargs or {})
    state = demo_state(settings.pop('notes', DEMO_MOTIF), bar=options.pop('bar', 0), **settings)
    event = from_text('实验输入', session_id=state.session_id, event_id=EVENT_ID)
    plan = make_plan(state, (event,), plan_id, options.get('strategy', 'RESPOND'),
                     list(options.get('actions', ())), options.get('preserve', ()),
                     options.get('transport', {}), options.get('valid_for_bars', 8), 'Fixture', [])
    return state, plan


BATTERY = [
    ('listen',                 dict(strategy='LISTEN'), {}),
    ('follow-vary-lead',       dict(strategy='FOLLOW', actions=[action('VARY_MOTIF', 'lead', .5)]), {}),
    ('respond-echo-motif',     dict(actions=[action('ECHO_MOTIF', 'motif', .7)]), {}),
    ('initiate-motif',         dict(strategy='INITIATE', actions=[action('INTRODUCE_MOTIF', 'lead')]), {}),
    ('preserve-keys',          dict(preserve=['layer:keys'], actions=[action('PRESERVE', 'keys')]), {}),
    ('build-band-d8',          dict(actions=[action('BUILD', 'band', 1., {'duration_bars': 8})]), {}),
    ('simplify-keys-s0',       dict(actions=[action('SIMPLIFY', 'keys', 0.)]), {}),
    ('adjust-drums-down',      dict(actions=[action('ADJUST_FEEL', 'drums', .5, {'level_delta': -.3})]), {}),
    ('adjust-bass-up',         dict(actions=[action('ADJUST_FEEL', 'bass', .5, {'level_delta': .3})]), {}),
    ('add-layer-bass',         dict(actions=[action('ADD_LAYER', 'bass')]), {}),
    ('remove-layer-drums',     dict(actions=[action('REMOVE_LAYER', 'drums')]), {}),
    ('introduce-guitar',       dict(actions=[action('INTRODUCE_LAYER', 'guitar')]), {}),
    ('set-key',                dict(actions=[action('SET_KEY', parameters={'tonic': 4, 'mode': 'minor'})]), {}),
    ('empty-actions',          dict(actions=[]), {}),
    ('combo-actions',          dict(actions=[action('ECHO_MOTIF', 'lead'), action('REMOVE_LAYER', 'bass'),
                                             action('SIMPLIFY', 'keys'), action('BUILD', 'drums')]), {}),
    ('preserve-tempo-key',     dict(preserve=['tempo', 'key'], actions=[action('ECHO_MOTIF', 'motif')]), {}),
    ('preserve-progression',   dict(preserve=['progression'], actions=[action('INTRODUCE_MOTIF', 'motif')]), {}),
    ('preserve-groove',        dict(preserve=['groove'], actions=[action('ADD_LAYER', 'bass')]), {}),
    ('bar3',                   dict(bar=2, actions=[action('ECHO_MOTIF', 'motif')]), {}),
    ('one-bar-build',          dict(valid_for_bars=1, actions=[action('BUILD', parameters={'duration_bars': 1})]), {}),
    ('sixteen-bar-echo',       dict(valid_for_bars=16, actions=[action('ECHO_MOTIF', 'motif')]), {}),
    ('tempo-transport',       dict(transport={'tempo_bpm': 120}), {}),
    ('energy0-simplify',       dict(actions=[action('SIMPLIFY', 'keys')]), dict(energy=0)),
    ('energy3-build',          dict(actions=[action('BUILD', 'band')]), dict(energy=3)),
    ('tempo60-echo',           dict(actions=[action('ECHO_MOTIF', 'motif')]), dict(tempo_bpm=60)),
    ('tempo180-echo',          dict(actions=[action('ECHO_MOTIF', 'motif')]), dict(tempo_bpm=180)),
    ('stopped',                dict(transport={'playing': False}), {}),
]

BATTERY_IDS = [b[0] for b in BATTERY]
BATTERY_PARAMS = [(b[1], b[2]) for b in BATTERY]


# ---------------------------------------------------------------------------
# Adapter 层：接受性 + 输出合法性 + 确定性
# ---------------------------------------------------------------------------

def check_frame(frame):
    """断言一帧条件 token 的形状与取值完全合法。"""
    assert set(frame) == {KEY, DKEY}, f'unexpected keys: {set(frame)}'
    roll, drum = frame[KEY], frame[DKEY]
    assert len(roll) == 128 and set(roll) <= {0, 1, 2}, f'bad piano roll: {roll}'
    assert len(drum) == 1 and drum[0] in (0, 1), f'bad drum token: {drum}'


def sweep_grid(adapter, pad_s=10.0):
    """覆盖 [0, 窗口结束+pad) 的时间网格，精确命中窗口起止边界与起音帧。"""
    start, end = adapter.start_s, adapter.end_s
    grid = [0.0, max(0.0, start - .04), start, start + .04,
            max(start + .04, end - .04), end, end + .04, end + pad_s]
    grid += list(np.arange(0.0, end + pad_s, .5))
    return sorted({round(t, 4) for t in grid if t >= 0})


def sweep(adapter, pad_s=10.0):
    """在完整时间网格上逐帧求值，返回帧列表。"""
    return [adapter.frame(t) for t in sweep_grid(adapter, pad_s)]


@pytest.mark.parametrize('plan_kwargs,state_kwargs', BATTERY_PARAMS, ids=BATTERY_IDS)
def test_adapter_accepts_every_bandplan(plan_kwargs, state_kwargs):
    state, plan = make_case('battery', plan_kwargs, state_kwargs)
    adapter = MRT2Adapter(state, plan)
    assert adapter.warnings == [f'Approximate only: {p}' for p in plan.to_dict()['preserve']]
    frames = sweep(adapter)
    assert frames, 'sweep produced no frames'
    for frame in frames:
        check_frame(frame)


@pytest.mark.parametrize('plan_kwargs,state_kwargs', BATTERY_PARAMS, ids=BATTERY_IDS)
def test_adapter_replay_is_deterministic(plan_kwargs, state_kwargs):
    state, plan = make_case('battery', plan_kwargs, state_kwargs)
    first = sweep(MRT2Adapter(state, plan))
    second = sweep(MRT2Adapter(state, plan))
    assert first == second, 'same inputs produced different conditioning tokens'


@pytest.mark.parametrize('plan_kwargs,state_kwargs', BATTERY_PARAMS, ids=BATTERY_IDS)
def test_adapter_window_boundaries_are_exact(plan_kwargs, state_kwargs):
    state, plan = make_case('battery', plan_kwargs, state_kwargs)
    adapter = MRT2Adapter(state, plan)
    start, end = adapter.start_s, adapter.end_s
    # 窗口开始前一瞬必须全零（bar>1 的计划才有窗口前区域）。
    if start > 0:
        before = adapter.frame(max(0.0, start - .04))
        assert not any(before[KEY]) and not any(before[DKEY]), f'non-zero before start at t={start - .04}'
    # 窗口结束一瞬及之后必须全零。
    for t in (end, end + .04, end + 10.0):
        frame = adapter.frame(t)
        assert not any(frame[KEY]) and not any(frame[DKEY]), f'non-zero after end at t={t}'
    # 窗口内至少出现过一次非零条件（playing 且有 action 时，信号必须真实存在）。
    if state.playing and plan.actions:
        fresh = MRT2Adapter(state, plan)
        ts = sorted({round(t, 4) for t in
                     [start, start + .04] + list(np.arange(start, min(end, start + 20.0), .5))})
        frames = [fresh.frame(t) for t in ts]
        assert any(any(f[KEY]) or any(f[DKEY]) for f in frames), 'no signal inside the plan window'


# ---------------------------------------------------------------------------
# Adapter 层：非法输入必须被明确拒绝
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('plan_kwargs,state_kwargs', [
    (dict(actions=[action('ECHO_MOTIF', 'motif')]), dict(notes=())),
    (dict(actions=[action('ADJUST_FEEL', 'band', parameters={'energy': .5})]), {}),
    (dict(actions=[action('VARY_MOTIF', 'motif')], preserve=[f'motif:{MOTIF_ID}']), {}),
    (dict(valid_for_bars=1, actions=[action('BUILD', parameters={'duration_bars': 4})]), {}),
    (dict(actions=[action('FILL', 'drums')]), {}),
    (dict(preserve=['tempo'], transport={'tempo_bpm': 110}), {}),
    (dict(preserve=['tuning']), {}),
], ids=['echo-no-motif', 'old-feel-parameter', 'vary-with-preserve',
        'build-duration-mismatch', 'fill-unavailable-offline', 'tempo-conflict', 'preserve-unknown'])
def test_adapter_rejects_invalid_plans(plan_kwargs, state_kwargs):
    with pytest.raises(ValueError):
        MRT2Adapter(*make_case('bad', plan_kwargs, state_kwargs))


def test_bandplan_from_dict_rejects_malformed_plans():
    state, plan = make_case('bad')
    data = plan.to_dict()
    with pytest.raises(ValueError):
        BandPlan.from_dict({**data, 'strategy': 'SHRED'})
    with pytest.raises(ValueError):
        BandPlan.from_dict({**data, 'trigger_event_ids': []})


def test_adapter_rejects_non_bar_boundary():
    state, plan = make_case('bad')
    data = plan.to_dict()
    data['effective_at']['beat'] = 2
    with pytest.raises(ValueError):
        MRT2Adapter(state, BandPlan.from_dict(data))


def test_adapter_rejects_invalid_timestamps():
    adapter = MRT2Adapter(*make_case('t', dict(actions=[action('ECHO_MOTIF', 'motif')])))
    for t in (-1.0, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            adapter.frame(t)


# ---------------------------------------------------------------------------
# 模型层：真实 MRT2 接受全部 BandPlan，且输出稳定
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def mrt():
    if os.environ.get('MRT2_INTEGRATION') != '1':
        pytest.skip('set MRT2_INTEGRATION=1 to run real-model tests (loads mrt2_small)')
    try:
        import mlx.core as mx
        from magenta_rt import MagentaRT2StdMlxfn
        from magenta_rt.config import MUSICCOCA
    except Exception as exc:
        pytest.skip(f'magenta_rt unavailable: {type(exc).__name__}: {exc}')
    mx.random.seed(42)
    t0 = time.perf_counter()
    try:
        model = MagentaRT2StdMlxfn(size='mrt2_small')
    except Exception as exc:
        pytest.skip(f'mrt2_small failed to load (mlx {mx.__version__}; the .mlxfn '
                    f'was exported with mlx 0.31.1 — try mlx 0.31.x in the venv): '
                    f'{type(exc).__name__}: {exc}')
    load_s = time.perf_counter() - t0
    style = model.tokenize_style(
        model.embed_style('warm funk ensemble, 96 BPM, C major')).tolist()
    assert len(style) == 12, f'expected 12 style tokens, got {len(style)}'
    return model, style, MUSICCOCA.key, load_s


def model_cond(mrt, plan_kwargs, state_kwargs, t=0.0):
    _, style, style_key, _ = mrt
    adapter = MRT2Adapter(*make_case('model', plan_kwargs, state_kwargs))
    return {style_key: style, **adapter.frame(adapter.start_s + t)}


def check_wav(wav, n_frames=1):
    samples = wav.samples
    assert samples.shape == (1920 * n_frames, 2), f'bad PCM shape {samples.shape}'
    assert np.isfinite(samples).all(), 'non-finite samples'
    peak = float(np.abs(samples).max())
    assert peak <= 1.0 + 1e-6, f'clipped/exploded output, peak={peak}'
    return samples


@pytest.mark.parametrize('plan_kwargs,state_kwargs', BATTERY_PARAMS, ids=BATTERY_IDS)
def test_model_accepts_every_bandplan(mrt, plan_kwargs, state_kwargs):
    model, _, _, _ = mrt
    cond = model_cond(mrt, plan_kwargs, state_kwargs)
    wav, _ = model.generate(conditioning=cond, frames=1)
    check_wav(wav)


def test_model_same_seed_same_output(mrt):
    model, _, _, _ = mrt
    import mlx.core as mx
    cond = model_cond(mrt, dict(actions=[action('ECHO_MOTIF', 'motif', .7)]), {})

    def run():
        mx.random.seed(42)
        wav, _ = model.generate(conditioning=cond, frames=3)
        return wav.samples

    a, b = run(), run()
    diff = float(np.abs(a - b).max())
    assert diff < 1e-6, f'same seed + same conditioning diverged, max|Δ|={diff}'


def test_model_stream_of_25_frames_stays_bounded(mrt):
    model, style, style_key, _ = mrt
    adapter = MRT2Adapter(*make_case('model', dict(actions=[
        action('ECHO_MOTIF', 'lead', .7), action('BUILD', 'drums', .5)])))
    cond = {style_key: style}
    frames, state_out, rms_list = [], None, []
    for i in range(25):
        cond.update(adapter.frame(i * .04))
        wav, state_out = model.generate(conditioning=cond, frames=1, state=state_out)
        samples = check_wav(wav)
        rms = float(np.sqrt(np.mean(samples ** 2)))
        assert rms < 1.0, f'frame {i} RMS out of bounds: {rms}'
        rms_list.append(rms)
        frames.append(samples)
    assert float(np.sqrt(np.mean(np.concatenate(frames) ** 2))) > 1e-6, 'stream is digital silence'
    assert len({np.round(f[0, 0], 6) for f in frames}) > 1, 'stream produced identical frames'


def test_model_different_bandplans_stay_bounded_and_differ(mrt):
    model, _, _, _ = mrt
    import mlx.core as mx
    # 三种条件在 t=0 处必须真实不同：基线排布已含 C-E-G-E 与鼓，
    # 直接 ECHO 相同动机或 FILL（仅末小节触发）都会产生与基线相同的 token，
    # 因此用基线之外的高低音动机制造确定的差异。
    variants = [
        ('empty', dict(actions=[]), {}),
        ('echo-high', dict(actions=[action('ECHO_MOTIF', 'motif', .7)]),
         dict(notes=(Note(102, 0, .5),))),
        ('echo-low', dict(actions=[action('ECHO_MOTIF', 'motif', .7)]),
         dict(notes=(Note(29, 0, .5),))),
    ]
    conds = [model_cond(mrt, pk, sk) for _, pk, sk in variants]
    rolls = [c[KEY] for c in conds]
    assert len({tuple(r) for r in rolls}) == 3, 'variant plans must differ in piano roll'
    outputs = []
    for cond in conds:
        mx.random.seed(7)  # 同 seed 隔离出条件的差异
        wav, _ = model.generate(conditioning=cond, frames=25)
        samples = check_wav(wav, n_frames=25)
        assert 1e-6 < float(np.sqrt(np.mean(samples ** 2))) < 1.0
        outputs.append(samples)
    diffs = [float(np.abs(a - b).mean()) for a, b in zip(outputs, outputs[1:])]
    assert max(diffs) > 1e-5, f'bandplan conditioning had no audible effect: {diffs}'


def test_model_tolerates_unknown_conditioning_keys(mrt):
    model, _, _, _ = mrt
    cond = model_cond(mrt, dict(actions=[action('ECHO_MOTIF', 'motif', .7)]), {})
    cond['no_such_key'] = [0] * 128  # 未知 key：当前实现静默忽略，不得崩溃
    wav, _ = model.generate(conditioning=cond, frames=1)
    check_wav(wav)
