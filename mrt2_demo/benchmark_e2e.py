"""Benchmark JamMate input ingestion, planning, MRT2 export and receipts."""
import argparse
import hashlib
import json
import math
import os
import platform
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import median
from time import perf_counter

import mido
import numpy as np
import soundfile as sf

from jammate.analysis.rhythm import analyze_rhythm_bar
from jammate.contracts import PlanError
from jammate.contracts.adapters import from_humming, from_rhythm, from_system, from_text
from jammate.music import BEATS_PER_BAR
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.backend_arrangement import LLMBackendArranger
from jammate.orchestration.llm import LLMAgent, ModelReply
from jammate.orchestration.llm_config import LLMConfig
from jammate.orchestration.manager import StateManager
from jammate.render.capabilities import BackendCapabilityError
from jammate.render.mrt2 import configured_backend
from jammate.render.runtime import create_runtime
from jammate.state import JamState

from .inference import FRAME_RATE, SAMPLE_RATE
from .jammate_bridge import generate

HUMMING_FIXTURE = Path(__file__).resolve().parents[1] / 'examples/input_events/source/humming_melody_v1.json'
DRUM_REQUEST = '鼓更猛一点'
BPM = 120


@dataclass(frozen=True)
class Scenario:
    name: str
    source: str = 'text'
    text: str = DRUM_REQUEST
    planner: str = 'rule'
    arrangement: str = 'midi'
    outcome: str = 'preview'
    action: str | None = 'BUILD'
    bpm_after: float = BPM
    bars: int = 4
    repeat: bool = True


SCENARIOS = (
    Scenario('text_midi'),
    Scenario('speech_midi', source='speech'),
    Scenario('rhythm_midi', source='rhythm', action='ADJUST_FEEL'),
    Scenario('humming_midi', source='humming', action='ECHO_MOTIF'),
    Scenario('tempo_midi', text='快一点', action=None, bpm_after=BPM + 8),
    Scenario('llm_midi', planner='scripted'),
    Scenario('rule_direct', arrangement='scripted'),
    Scenario('llm_direct', planner='scripted', arrangement='scripted'),
    Scenario('long_midi', planner='scripted', bars=16, repeat=False),
    Scenario('strict_refusal', outcome='strict', repeat=False),
    Scenario('cancel_then_stop', outcome='cancel', repeat=False),
    Scenario('failure_then_retry', outcome='retry', repeat=False),
    Scenario('stop', source='system', outcome='stop', action=None, bars=1, repeat=False),
)
LIVE_SCENARIO = Scenario('live_llm_direct', planner='live', arrangement='live', action=None, repeat=False)


class InjectedRenderFailure(RuntimeError):
    """Test an actual Runtime failure and retry before touching the model."""


class ScriptedProvider:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, schema, timeout_s):
        return ModelReply(json.dumps(self.reply), 'benchmark-scripted-provider', {})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def timed(timings, stage, call, *args, **kwargs):
    start = perf_counter()
    try:
        return call(*args, **kwargs)
    finally:
        timings[stage] = perf_counter() - start


def input_event(scenario, session_id, folder):
    identity = {'session_id': session_id, 'event_id': 'input'}
    if scenario.source == 'system':
        return from_system('stop', **identity)
    if scenario.source == 'humming':
        melody = json.loads(HUMMING_FIXTURE.read_text(encoding='utf-8'))
        return from_humming(melody, started_at=0, **identity)
    if scenario.source == 'rhythm':
        sample_rate = 22050
        samples = np.zeros(round(sample_rate * BEATS_PER_BAR * 60 / BPM), dtype=np.float32)
        rng = np.random.default_rng(3800)
        size = round(sample_rate * .032)
        envelope = np.exp(-np.arange(size) / (size / 4))
        for beat in (.25, 1, 1.5, 2.5, 3.25):
            start = round(beat * 60 / BPM * sample_rate)
            samples[start:start + size] = .65 * rng.uniform(-1, 1, size) * envelope
        path = folder / 'rhythm-source.wav'
        sf.write(path, samples, sample_rate, subtype='PCM_16')
        pcm, rate = sf.read(path, dtype='float32')
        context = analyze_rhythm_bar(pcm, rate, BPM)
        require(len(context.onset_beats) == 5 and context.confidence >= .65,
                'JamMate did not detect the five rhythm fixture attacks')
        return from_rhythm(context, audio_duration_s=len(pcm) / rate, tempo_source='session',
                           started_at=0, audio_ref=path.name,
                           confidence_kind='grid_alignment_heuristic', **identity)
    return from_text(scenario.text, source=scenario.source, **identity)


def make_agent(scenario, live_config):
    if scenario.planner == 'rule':
        return RuleAgent()
    if scenario.planner == 'live':
        return LLMAgent(live_config, fallback='error')
    decision = {
        'strategy': 'FOLLOW', 'valid_for_bars': scenario.bars,
        'preserve': ['tempo', 'key', 'layer:bass', 'layer:keys', 'layer:lead'],
        'actions': [{'action': 'BUILD', 'scope': 'drums', 'strength': .5,
                     'parameters': {'duration_bars': 4}}],
        'transport': {'playing': None, 'tempo_bpm': None},
        'reason': 'Reproducible benchmark decision', 'unresolved': [],
    }
    return LLMAgent(LLMConfig(api_key='benchmark-fixture'), provider=ScriptedProvider(decision), fallback='error')


def make_arranger(scenario, live_config):
    if scenario.arrangement == 'midi':
        return None
    if scenario.arrangement == 'live':
        return LLMBackendArranger(live_config, 'mrt2')
    reply = {
        'style': 'warm instrumental trio, punchy drums and electric bass',
        'pitch_spans': [{'frame': frame, 'length': 6, 'pitch': pitch}
                        for frame, pitch in ((0, 48), (13, 55), (25, 60), (38, 52))],
        'drum_spans': [{'frame': frame, 'length': 2} for frame in (0, 13, 25, 38)],
    }
    return LLMBackendArranger(LLMConfig(api_key='benchmark-fixture'), 'mrt2', provider=ScriptedProvider(reply))


def check_receipt(runtime, plan, observed):
    artifacts = runtime.last_artifacts
    receipt = json.loads((artifacts / 'receipt.json').read_text(encoding='utf-8'))
    data = plan.to_dict()
    require(json.loads((artifacts / 'plan.json').read_text(encoding='utf-8')) == data,
            'Persisted BandPlan differs from the staged plan')
    require(json.loads((artifacts / 'state_observed.json').read_text(encoding='utf-8')) == observed.to_dict(),
            'Persisted observation differs from the execution snapshot')
    bar = data['effective_at']['bar']
    require((receipt['session_id'], receipt['plan_id'], receipt['based_on_state_version'],
             receipt['effective_bar'], receipt['completed_bar']) ==
            (observed.session_id, plan.plan_id, observed.state_version, bar, bar + data['valid_for_bars']),
            'Receipt does not match the staged plan/session/window')
    require(not receipt.get('persistence_warnings'), 'Runtime reported a persistence failure')
    return receipt


def check_audio(path, receipt, duration, *, silent):
    info = sf.info(path)
    pcm, rate = sf.read(path, dtype='float32', always_2d=True)
    require(info.format == 'WAV' and info.subtype == 'PCM_16'
            and rate == SAMPLE_RATE and pcm.shape == (round(duration * SAMPLE_RATE), 2)
            and np.isfinite(pcm).all(), 'Invalid duration, PCM format or audio samples')
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    require(rms == 0 if silent else rms > 1e-7, 'WAV failed the expected silence/non-silence check')
    require(receipt['wav']['channels'] == 2 and receipt['wav']['sample_rate'] == rate
            and receipt['wav']['duration_s'] == len(pcm) / rate
            and math.isclose(receipt['wav']['rms'], rms, abs_tol=1e-10), 'WAV and receipt disagree')
    return {**receipt['wav'], 'samples_per_channel': len(pcm),
            'full_scale_samples': int(np.count_nonzero((pcm <= -1) | (pcm >= 32767 / 32768)))}


def check_preview(runtime, receipt, manager, plan, observed, scenario, duration):
    artifacts = runtime.last_artifacts
    require(receipt['phase'] == 'preview' and receipt['artifact_generated'] is True
            and receipt['succeeded'] is False and receipt['state_committed'] is False
            and manager.state == observed and manager.plan_status(plan.plan_id) == 'failed',
            'Preview incorrectly changed the applied state')
    require(receipt['inference'] == 'real_magenta_rt' and receipt['frames'] == math.ceil(duration * FRAME_RATE),
            'Expected real MRT2 inference at the requested frame count')
    require(json.loads((artifacts / 'state_applied.json').read_text(encoding='utf-8')) == observed.to_dict(),
            'Preview persisted a different applied state')
    midi, conditions = artifacts / 'band.mid', artifacts / 'conditioning.json'
    if scenario.arrangement == 'midi':
        require(receipt['conditioning'] == 'text_and_midi' and receipt['midi_role'] == 'conditioning_not_transcription'
                and receipt['midi_verification']['note_count'] > 0 and not conditions.exists(),
                'Expected verified MIDI conditioning artifacts')
        require(receipt['midi_sha256'] == hashlib.sha256(midi.read_bytes()).hexdigest()
                and math.isclose(mido.MidiFile(midi).length, duration, abs_tol=.002),
                'Conditioning MIDI fingerprint or duration does not match')
    else:
        data = json.loads(conditions.read_text(encoding='utf-8'))
        require(receipt['conditioning'] == 'text_and_direct_frames' and receipt['midi_sha256'] is None
                and receipt['arrangement']['status'] == 'accepted' and not midi.exists()
                and data['frame_rate'] == FRAME_RATE and data['pitch_spans'] and data['drum_spans'],
                'Expected the validated pitch and drum conditions from the direct arranger')


def expect_rejection(call, expected_error):
    try:
        call()
    except expected_error:
        return
    raise ValueError(f'Expected {expected_error.__name__}')


def run_trial(scenario, folder, trial, live_config):
    timings = trial['timings_s']
    manager = StateManager(JamState(session_id=scenario.name, tempo_bpm=BPM))
    event = timed(timings, 'input', input_event, scenario, manager.state.session_id, folder)
    event.write(folder / 'input.json')
    events = timed(timings, 'ingest', manager.ingest, [event])
    require(events == (event,), 'JamMate did not accept the benchmark event')
    observed = manager.state
    agent = make_agent(scenario, live_config)
    plan = timed(timings, 'plan', agent.decide, events, observed, plan_id='benchmark')
    data = plan.to_dict()
    require(not data['unresolved'] and data['trigger_event_ids'] == [event.event_id],
            'Agent did not produce an executable plan for the benchmark event')
    require(scenario.action is None or any(action['action'] == scenario.action for action in plan.actions),
            f'Expected {scenario.action} in the BandPlan')
    bpm = data['transport'].get('tempo_bpm', observed.tempo_bpm)
    require(bpm == scenario.bpm_after and (scenario.planner == 'live' or data['valid_for_bars'] == scenario.bars),
            'Agent did not produce the expected tempo or bar count')
    duration = data['valid_for_bars'] * BEATS_PER_BAR * 60 / bpm
    trial.update(plan={'bpm': bpm, 'bars': data['valid_for_bars'], 'actions': plan.actions},
                 generator_calls=0)
    timed(timings, 'stage', manager.stage, plan)

    def measured_generator(prompt, **kwargs):
        trial['generator_calls'] += 1
        if scenario.outcome == 'retry' and trial['generator_calls'] == 1:
            raise InjectedRenderFailure('benchmark render failure before inference')
        result = timed(timings, 'generator', generate, prompt, **kwargs)
        if scenario.outcome == 'cancel':
            stop = from_system('stop', session_id=observed.session_id, event_id='stop')
            stop.write(folder / 'replacement-input.json')
            manager.ingest([stop])
        return result

    runtime = create_runtime(folder, backend='mrt2', allow_preview=scenario.outcome != 'strict',
                             backend_arranger=make_arranger(scenario, live_config), generator=measured_generator)
    started = perf_counter()
    if scenario.outcome == 'strict':
        expect_rejection(lambda: runtime.execute(manager, plan), BackendCapabilityError)
        receipt = check_receipt(runtime, plan, observed)
        require(trial['generator_calls'] == 0 and not receipt['artifact_generated']
                and not (runtime.last_artifacts / 'band.wav').exists() and manager.state == observed
                and manager.plan_status(plan.plan_id) == 'failed' and not receipt['state_committed'],
                'Strict rejection generated audio or changed the applied state')
    elif scenario.outcome == 'cancel':
        expect_rejection(lambda: runtime.execute(manager, plan), PlanError)
        cancelled = check_receipt(runtime, plan, observed)
        require(trial['generator_calls'] == 1 and cancelled['inference'] == 'real_magenta_rt'
                and manager.plan_status(plan.plan_id) == 'cancelled'
                and not cancelled['state_committed'] and manager.state.playing
                and manager.state.layers == observed.layers and manager.state.state_version == observed.state_version + 1,
                'Superseded audio was applied or later input was lost')
        trial['cancelled_artifacts'] = runtime.last_artifacts.name
        observed = manager.state
        plan = RuleAgent().decide(manager.current_batch(), observed, plan_id='replacement-stop')
        manager.stage(plan)
        runtime.execute(manager, plan)
        receipt = check_receipt(runtime, plan, observed)
        duration = BEATS_PER_BAR * 60 / observed.tempo_bpm
    else:
        if scenario.outcome == 'retry':
            expect_rejection(lambda: runtime.execute(manager, plan), InjectedRenderFailure)
            failed = check_receipt(runtime, plan, observed)
            require(manager.state == observed and manager.plan_status(plan.plan_id) == 'failed'
                    and not failed['state_committed'] and not failed['artifact_generated']
                    and not (runtime.last_artifacts / 'band.wav').exists(), 'Failed generation affected session state')
            trial['failed_artifacts'] = runtime.last_artifacts.name
            plan = agent.decide(manager.current_batch(), observed, plan_id='benchmark-retry')
            manager.stage(plan)
        runtime.execute(manager, plan)
        receipt = check_receipt(runtime, plan, observed)
    timings['runtime'] = perf_counter() - started

    trial.update(artifacts=runtime.last_artifacts.name, receipt_phase=receipt['phase'],
                 state_committed=receipt['state_committed'], inference=receipt.get('inference'),
                 plan_status=manager.plan_status(plan.plan_id))
    if scenario.outcome == 'strict':
        return
    silent = scenario.outcome in {'stop', 'cancel'}
    if silent:
        require(trial['generator_calls'] == (1 if scenario.outcome == 'cancel' else 0)
                and receipt['succeeded'] and receipt['state_committed']
                and receipt['inference'] == 'host_silence_no_model' and not manager.state.playing
                and manager.state.state_version == observed.state_version + 1
                and manager.plan_status(plan.plan_id) == 'applied', 'Stop did not commit host silence')
    else:
        require(trial['generator_calls'] == (2 if scenario.outcome == 'retry' else 1),
                'Unexpected generator call count')
        check_preview(runtime, receipt, manager, plan, observed, scenario, duration)
        trial.update(conditioning=receipt['conditioning'], render_rtf=timings['generator'] / duration)
    trial['audio'] = check_audio(runtime.last_artifacts / 'band.wav', receipt, duration, silent=silent)


def package_versions(backend):
    versions = {}
    for package in ('magenta-rt', 'mlx' if backend == 'mlx' else 'jax', 'numpy', 'soundfile', 'librosa'):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return versions


def aggregate(results):
    successful = [row for row in results if row['status'] == 'pass']
    metrics = {}
    for field in ('end_to_end_s', 'render_rtf'):
        values = [row[field] for row in successful if field in row]
        if values:
            metrics[field] = {'samples': len(values), 'median': median(values),
                              'p95': float(np.percentile(values, 95)), 'max': max(values)}
    return {'passed': len(successful), 'failed': len(results) - len(successful), 'metrics': metrics}


def run_suite(output, repeats, live_config=None, *, scenarios=SCENARIOS):
    output.mkdir(parents=True, exist_ok=False)
    if live_config is not None:
        scenarios = (*scenarios, LIVE_SCENARIO)
    model = os.environ.get('JAMMATE_MRT2_MODEL', 'mrt2_small')
    backend = configured_backend(model)
    summary = {'status': 'running', 'backend': backend, 'model': model,
               'python': platform.python_version(), 'platform': platform.platform(), 'versions': package_versions(backend),
               'live_llm': live_config is not None, 'repeats': repeats, 'scenarios': {}}
    summary_path = output / 'summary.json'
    write_json(summary_path, summary)
    all_trials = []
    for scenario in scenarios:
        results = []
        for index in range(1, (repeats if scenario.repeat else 1) + 1):
            folder = output / scenario.name / f'trial-{index:02}'
            folder.mkdir(parents=True)
            trial = {'path': str(folder.relative_to(output)), 'timings_s': {}}
            started = perf_counter()
            try:
                run_trial(scenario, folder, trial, live_config)
            except Exception as error:
                message = str(error)
                if live_config is not None:
                    message = message.replace(live_config.api_key, '[redacted]')
                trial.update(status='fail', error=f'{type(error).__name__}: {message}')
            else:
                trial['status'] = 'pass'
            trial['end_to_end_s'] = perf_counter() - started
            write_json(folder / 'trial.json', trial)
            results.append(trial)
            all_trials.append(trial)
            summary['scenarios'][scenario.name] = {
                'source': scenario.source, 'planner': scenario.planner, 'arrangement': scenario.arrangement,
                **aggregate(results), 'trials': results,
            }
            write_json(summary_path, summary)
            print(f"{trial['status'].upper():4} {scenario.name} {index} {trial['end_to_end_s']:.2f}s"
                  + (f"  {trial['error']}" if 'error' in trial else ''), flush=True)
    summary.update(aggregate(all_trials))
    summary['status'] = 'failed' if summary['failed'] else 'passed'
    write_json(summary_path, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='new directory for case artifacts and summary.json')
    parser.add_argument('--trials', type=int, default=3, help='repetitions of each standard preview scenario')
    parser.add_argument('--live-llm', action='store_true', help='also run one case with the configured LLM API')
    parser.add_argument('--llm-config', type=Path, help='private JamMate config for --live-llm')
    args = parser.parse_args()
    if args.trials < 1:
        parser.error('--trials must be positive')
    if args.out.exists():
        parser.error('--out must be a new directory')
    if args.llm_config and not args.live_llm:
        parser.error('--llm-config requires --live-llm')
    live = LLMConfig.load(args.llm_config) if args.live_llm else None
    summary = run_suite(args.out, args.trials, live)
    print(f"{summary['passed']} passed, {summary['failed']} failed  {args.out / 'summary.json'}")
    return int(summary['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
