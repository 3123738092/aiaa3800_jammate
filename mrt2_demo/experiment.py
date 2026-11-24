"""Preserve experiment orchestrator.

For each fixture: identical input events feed (1) the deterministic MIDI
baseline (control / analyzer noise floor) and (2) MRT2 runs. Evidence only --
missing measurements stay null, never faked.
"""
import json
import subprocess
import sys
from dataclasses import asdict

import numpy as np
import soundfile as sf

from .run import ROOT
from . import fixtures as fx
from . import analyze_preserve as ap
from jammate.render.synth import render

PROMPT = 'minimal clean synth lead, no drums, 96 BPM, C major'
RUNS = [
    ('a', 20, 'motif pitch/onset'),
    ('b', 20, 'harmony C/Am/F/G'),
    ('c-straight', 20, 'groove: straight 16ths'),
    ('c-swing', 20, 'groove: 66% swing 16ths'),
    ('d', 60, 'BPM stability / drift'),
]


def run_mrt2(out_dir, name, seconds, seed):
    cmd = [sys.executable, '-m', 'mrt2_demo.run', '--mode', 'midi', '--fixture', name,
           '--seconds', str(seconds), '--seed', str(seed), '--prompt', PROMPT,
           '--out', str(out_dir)]
    code = subprocess.run(cmd, cwd=ROOT.parent, check=False).returncode
    report = json.loads((out_dir / 'report.json').read_text()) if (out_dir / 'report.json').exists() else {}
    return code, report


def main():
    out = ROOT / 'results' / 'preserve'
    out.mkdir(parents=True, exist_ok=True)
    evidence = {}
    for name, seconds, label in RUNS:
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        notes = fx.FIXTURES[name]()
        (d / 'input-notes.json').write_text(json.dumps([asdict(n) for n in notes], indent=2))

        # control: deterministic synth baseline from the SAME notes
        pcm = render(notes, fx.BPM, sr=48000, total_s=seconds)
        sf.write(d / 'midi-baseline.wav', np.column_stack([pcm, pcm]), 48000)
        control = ap.analyze(d / 'midi-baseline.wav', notes, fx.BPM, name)
        (d / 'midi-baseline.analysis.json').write_text(json.dumps(control, indent=2))

        # MRT2: 3 seeds for fixture a (sampler determinism check), 1 elsewhere
        seeds = [42, 43, 44] if name == 'a' else [42]
        runs = []
        for seed in seeds:
            od = d / f'seed-{seed}'
            code, report = run_mrt2(od, name, seconds, seed)
            if code or report.get('status') != 'completed':
                runs.append({'seed': seed, 'status': report.get('status', 'failed'),
                             'error': report.get('error')})
                continue
            a = ap.analyze(od / 'audio.wav', notes, fx.BPM, name)
            a.update(seed=seed, status=report['status'],
                     generation_rtf=report.get('generation_rtf'))
            (od / 'preserve.analysis.json').write_text(json.dumps(a, indent=2))
            runs.append(a)
        evidence[name] = {'label': label, 'seconds': seconds, 'control': control, 'runs': runs}
        print(f'[{name}] done: {len(runs)} run(s)', flush=True)

    (out / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    print_table(evidence)


def print_table(ev):
    def f(v, nd=1):
        return 'null' if v is None else f'{v:.{nd}f}'

    print('\n===== preserve evidence (MIDI baseline = analyzer noise floor) =====\n')
    for name, block in ev.items():
        c, runs = block['control'], block['runs']
        r = runs[0] if runs else {}
        print(f"[{name}] {block['label']} ({block['seconds']}s)")
        print(f"  onsets: expected={c['expected_onsets']} "
              f"baseline_matched={c['matched_onsets']} mrt2_matched={r.get('matched_onsets')}")
        print(f"  onset误差 median: baseline={f(c['onset_error_ms_median'])} ms "
              f"mrt2={f(r.get('onset_error_ms_median'))} ms "
              f"(p95: {f(c['onset_error_ms_p95'])} / {f(r.get('onset_error_ms_p95'))})")
        print(f"  onset误差均值(有符号): baseline={f(c['onset_error_ms_mean_signed'])} ms "
              f"mrt2={f(r.get('onset_error_ms_mean_signed'))} ms")
        print(f"  BPM估计: baseline={f(c['bpm_estimate'])} mrt2={f(r.get('bpm_estimate'))}")
        if 'pitch' in c and 'pitch' in r:
            pc, pr = c['pitch'], r.get('pitch', {})
            print(f"  音高: voiced={f(pc['voiced_ratio'],2)}/{f(pr.get('voiced_ratio'),2)} "
                  f"accuracy={f(pc['pitch_accuracy'],2)}/{f(pr.get('pitch_accuracy'),2)} "
                  f"octave_acc={f(pc['octave_accuracy'],2)}/{f(pr.get('octave_accuracy'),2)} "
                  f"median_err={f(pc['median_error_semitones'],2)}/{f(pr.get('median_error_semitones'),2)}半音")
        if 'drift_rate_ms_per_s' in r:
            print(f"  漂移: mrt2={f(r['drift_rate_ms_per_s'],2)} ms/s "
                  f"(first10s={f(r.get('onset_error_first10s_ms'))} last10s={f(r.get('onset_error_last10s_ms'))})")
        if 'harmony' in c and 'harmony' in r:
            for hc, hr in zip(c['harmony'], r.get('harmony', [])):
                print(f"  和声[{hc['segment']}]: concentration baseline={f(hc['concentration'],2)} "
                      f"mrt2={f(hr.get('concentration'),2)}  mrt2_top3={hr.get('top3')}")
        if 'offbeat' in c and 'offbeat' in r:
            oc, orr = c['offbeat'], r.get('offbeat', {})
            print(f"  groove offbeat相位(beat内): baseline={f(oc['mean_phase_beats'],3)} "
                  f"mrt2={f(orr.get('mean_phase_beats'),3)}  (std {f(oc['std_phase_beats'],3)}/{f(orr.get('std_phase_beats'),3)})")
        if len(runs) > 1:
            h = [json.dumps({'seed': x.get('seed'), 'matched': x.get('matched_onsets'),
                             'accuracy': (x.get('pitch') or {}).get('pitch_accuracy')})
                 for x in runs]
            print(f"  seed重复性: {h}")
        print()


if __name__ == '__main__':
    main()
