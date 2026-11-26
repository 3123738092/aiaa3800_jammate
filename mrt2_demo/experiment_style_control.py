"""Style-control benchmark: how strongly do text / MIDI / audio move the output?

Single-variable ablation on one common base:
  base        style slot = text('warm funk ensemble, 96 BPM, C major'); no note slot
  text-*      only the prompt text changes (4 styles, shared '96 BPM, C major' suffix)
  audio-*     prompt removed; style slot = MusicCoCa audio embedding of a wav:
              self: base's own output (audio-encoder self-consistency check)
              synth: jammate baseline synth (neutral reference)
              violin/metal/flute/techno: the matching text-* output (cross-modal check)
  midi-*      prompt unchanged; note slot added (pianoroll + drums):
              empty: all zeros (channel-presence control)
              drums: drums only (pitch-content control)
              funk/low/high: ECHO_MOTIF piano roll at 3 registers
              chromatic: held cluster pitches 48-60 (content extreme)

mlxfn is deterministic: identical conditioning => bit-identical output, so every
delta is a pure conditioning effect. Suite reproducibility is re-checked.
"""
import hashlib, json, shutil, time
from pathlib import Path

import numpy as np
import soundfile as sf
import mlx.core as mx
from magenta_rt import MagentaRT2StdMlxfn as Model
from magenta_rt.audio import Waveform
from magenta_rt.config import MUSICCOCA
from jammate.music import Note
from .adapter import MRT2Adapter
from .fixtures import demo_plan
from .run import ROOT

FRAMES, SR = 500, 48000          # 20 s at 25 Hz
P0 = 'warm funk ensemble, 96 BPM, C major'
STYLE_WORDS = dict(
    funk=P0,
    violin='solo violin, slow legato, sparse, 96 BPM, C major',
    metal='distorted metal guitar, aggressive, fast, 96 BPM, C major',
    flute='soft flute, gentle airy melody, 96 BPM, C major',
    techno='deep techno, heavy bass pulse, 96 BPM, C major',
)
MOTIFS = dict(
    funk=(Note(60, 0, .5), Note(64, .5, .5), Note(67, 1, 1), Note(64, 2, 1)),
    low=(Note(24, 0, .5), Note(28, .5, .5), Note(31, 1, 1), Note(28, 2, 1)),
    high=(Note(84, 0, .5), Note(88, .5, .5), Note(91, 1, 1), Note(88, 2, 1)),
)
OUT = ROOT / 'results' / 'style-ablation'


def make_adapter(motif):
    return MRT2Adapter(*demo_plan(motif))


def render(tag, cond_fn, model, extra=None):
    d = OUT / tag
    d.mkdir(parents=True, exist_ok=True)
    cache = None
    t0 = time.perf_counter()
    frames = []
    for i in range(FRAMES):
        wav, cache = model.generate(conditioning=cond_fn(i), frames=1, state=cache)
        frames.append(wav.samples)
    audio = np.concatenate(frames)
    sf.write(d / 'audio.wav', audio, SR, subtype='PCM_16')
    y = audio.mean(axis=1)
    spec = np.abs(np.fft.rfft(y)) ** 2
    fr = np.fft.rfftfreq(len(y), 1 / SR)
    m = dict(tag=tag, centroid_hz=float((spec * fr).sum() / spec.sum()),
             rms=float(np.sqrt(np.mean(y * y))), peak=float(np.abs(audio).max()),
             wall_s=time.perf_counter() - t0,
             md5=hashlib.md5((d / 'audio.wav').read_bytes()).hexdigest(),
             path=str(d / 'audio.wav'))
    if extra:
        m.update(extra)
    (d / 'run.json').write_text(json.dumps(m, indent=2))
    print(f'{tag:15s} centroid={m["centroid_hz"]:5.0f}Hz rms={m["rms"]:.4f} wall={m["wall_s"]:.1f}s', flush=True)
    return m


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def mono(path):
    return sf.read(str(path), always_2d=True)[0].mean(axis=1)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    t = time.perf_counter()
    model = Model(size='mrt2_small')
    print(f'model loaded in {time.perf_counter() - t:.1f}s', flush=True)

    def embed(value):
        return model.tokenize_style(model.embed_style(value, use_mapper=isinstance(value, str))).tolist()

    metrics = {}

    # Phase 1: text channel (base is the funk prompt).
    for name, prompt in STYLE_WORDS.items():
        tag = 'base' if name == 'funk' else f'text-{name}'
        tok = embed(prompt)
        metrics[tag] = render(tag, lambda i, t=tok: {MUSICCOCA.key: t}, model,
                              extra=dict(channel='text', changed_vs_base='prompt text', prompt=prompt))

    # Phase 2: audio channel. Refs = base's own output, neutral synth, and the
    # text-* outputs of the same nominal style (cross-modal convergence check).
    refs = dict(self=OUT / 'base' / 'audio.wav',
                synth=ROOT / 'results' / 'midi-baseline.wav',
                violin=OUT / 'text-violin' / 'audio.wav',
                metal=OUT / 'text-metal' / 'audio.wav',
                flute=OUT / 'text-flute' / 'audio.wav',
                techno=OUT / 'text-techno' / 'audio.wav')
    for name, path in refs.items():
        tok = embed(Waveform.from_file(str(path)))
        tag = f'audio-{name}'
        metrics[tag] = render(tag, lambda i, t=tok: {MUSICCOCA.key: t}, model,
                              extra=dict(channel='audio', changed_vs_base='style tokens from wav',
                                         ref=str(path)))

    # Phase 3: midi channel (prompt stays P0; note slot varies).
    tok = embed(P0)
    rolls = {}
    for k, motif in MOTIFS.items():
        probe = make_adapter(motif)
        r0 = probe.frame(0)
        rolls[k] = dict(active_pitches=[p for p, v in enumerate(r0['pianoroll_with_onsets_tokens']) if v],
                        drums=r0['drum_pianoroll_tokens'])
    held = [1 if p in range(48, 61) else 0 for p in range(128)]
    rolls['chromatic'] = dict(active_pitches=list(range(48, 61)), drums=[1])
    assert len({tuple(rolls[k]['active_pitches']) for k in MOTIFS} | {tuple(rolls['chromatic']['active_pitches'])}) == 4

    def empty(i):
        return {MUSICCOCA.key: tok, 'pianoroll_with_onsets_tokens': [0] * 128,
                'drum_pianoroll_tokens': [0]}

    def drums(i):
        return {MUSICCOCA.key: tok, 'pianoroll_with_onsets_tokens': [0] * 128,
                'drum_pianoroll_tokens': [1]}

    def chromatic(i):
        roll = [2 if (i == 0 and held[p]) else held[p] for p in range(128)]
        return {MUSICCOCA.key: tok, 'pianoroll_with_onsets_tokens': roll,
                'drum_pianoroll_tokens': [1]}

    metrics['midi-empty'] = render('midi-empty', empty, model,
                                   extra=dict(channel='midi', changed_vs_base='note slot added, all zeros',
                                              active_pitches=[], drums=[0]))
    metrics['midi-drums'] = render('midi-drums', drums, model,
                                   extra=dict(channel='midi', changed_vs_base='note slot: drums only',
                                              active_pitches=[], drums=[1]))
    for k, motif in MOTIFS.items():
        adapter = make_adapter(motif)
        metrics[f'midi-{k}'] = render(f'midi-{k}',
                                      lambda i, a=adapter: {MUSICCOCA.key: tok, **a.frame(i * .04)},
                                      model,
                                      extra=dict(channel='midi', changed_vs_base='note slot: piano roll',
                                                 active_pitches=rolls[k]['active_pitches'],
                                                 drums=rolls[k]['drums']))
    metrics['midi-chromatic'] = render('midi-chromatic', chromatic, model,
                                       extra=dict(channel='midi', changed_vs_base='note slot: held cluster 48-60',
                                                  active_pitches=rolls['chromatic']['active_pitches'],
                                                  drums=rolls['chromatic']['drums']))

    # Analysis.
    y0 = mono(metrics['base']['path'])

    def delta(a, b):
        return float(np.abs(mono(metrics[a]['path']) - mono(metrics[b]['path'])).max())

    deltas = {tag: delta(tag, 'base') for tag in metrics if tag != 'base'}
    cross = {'self: audio-self vs base': delta('audio-self', 'base')}
    for s in ['violin', 'metal', 'flute', 'techno']:
        cross[f'{s}: text vs audio (same style)'] = delta(f'text-{s}', f'audio-{s}')
    midi_pairs = {
        'channel presence: midi-empty vs base': delta('midi-empty', 'base'),
        'drums content: midi-drums vs midi-empty': delta('midi-drums', 'midi-empty'),
        'pitch content: midi-funk vs midi-drums': delta('midi-funk', 'midi-drums'),
        'register low: midi-low vs midi-funk': delta('midi-low', 'midi-funk'),
        'register high: midi-high vs midi-funk': delta('midi-high', 'midi-funk'),
        'content extreme: midi-chromatic vs midi-funk': delta('midi-chromatic', 'midi-funk'),
    }
    suite = ROOT / 'results' / 'suite'
    sanity = {'base_vs_suite_text-1': metrics['base']['md5'] == md5(suite / 'text-1' / 'audio.wav'),
              'midi-funk_vs_suite_midi-1': metrics['midi-funk']['md5'] == md5(suite / 'midi-1' / 'audio.wav'),
              'audio-synth_vs_suite_audio-1': metrics['audio-synth']['md5'] == md5(suite / 'audio-1' / 'audio.wav')}
    summary = dict(
        design='Single-variable ablation on one common base. text: prompt words only (shared suffix). '
               'audio: style slot filled by MusicCoCa audio encoder (self/synth/text-twins). '
               'midi: note slot added, content varies (empty/drums/3 registers/chromatic). '
               'mlxfn deterministic => deltas are pure conditioning effects.',
        base=dict(prompt=P0, frames=FRAMES, sample_rate=SR),
        runs=metrics, delta_vs_base=deltas, cross_modal_same_style=cross,
        midi_decomposition=midi_pairs, suite_reproducibility=sanity,
        scope='Not covered: playback/latency (results/suite), bandplan robustness '
              '(test_bandplan_robustness.py), CFG-scale sweeps, subjective listening.')
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
    print('\n== delta vs base (max abs sample diff, mono) ==')
    for k, v in deltas.items():
        print(f'{k:15s} {v:.4f}')
    print('\n== cross-modal, same nominal style ==')
    for k, v in cross.items():
        print(f'{k:34s} {v:.4f}')
    print('\n== midi decomposition (pairwise deltas) ==')
    for k, v in midi_pairs.items():
        print(f'{k:40s} {v:.4f}')
    print('\n== suite reproducibility ==')
    for k, v in sanity.items():
        print(f'{k:30s} {v}')


if __name__ == '__main__':
    main()
