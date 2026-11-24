"""Preserve-following diagnostics: pitch / BPM / groove / harmony.

Explicitly diagnostic, not a musical-preservation certification. Every metric
is reported alongside the same metric measured on the deterministic MIDI
baseline (the analyzer's noise floor); only the difference is attributable to
the generative model.
"""
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import find_peaks


# --------------------------------------------------------------------------
# onset detection (energy flux, 5 ms hop, parabolic refinement)
# --------------------------------------------------------------------------
def detect_onsets(y, sr, hop_ms=5, rel_height=0.2, min_dist_ms=30):
    hop = max(1, int(sr * hop_ms / 1000))
    n = len(y) // hop * hop
    if n < hop * 2:
        return np.array([])
    rms = np.sqrt(np.mean(y[:n].reshape(-1, hop) ** 2, axis=1))
    flux = np.maximum(0, np.diff(rms, prepend=rms[0]))
    if flux.max() <= 0:
        return np.array([])
    peaks, _ = find_peaks(flux, height=max(flux.max() * rel_height, 1e-6),
                          distance=max(1, int(min_dist_ms / hop_ms)))
    times = peaks.astype(float) * hop / sr
    for k, p in enumerate(peaks):
        if 0 < p < len(flux) - 1:
            a, b, c = flux[p - 1], flux[p], flux[p + 1]
            denom = a - 2 * b + c
            if denom != 0:
                times[k] += (0.5 * (a - c) / denom) * hop / sr
    return times


def match_onsets(onsets, expected, window_s=0.14):
    """Greedy 1:1 match of detected onsets to expected note onsets."""
    exp = np.sort(np.asarray(expected, float))
    used, errors, matched_times, matched_expected = set(), [], [], []
    for t in sorted(onsets):
        i = np.searchsorted(exp, t)
        cands = [j for j in (i - 1, i, i + 1) if 0 <= j < len(exp) and j not in used]
        if not cands:
            continue
        j = min(cands, key=lambda j: abs(t - exp[j]))
        if abs(t - exp[j]) <= window_s:
            used.add(j)
            errors.append(t - exp[j])
            matched_times.append(t)
            matched_expected.append(exp[j])
    return np.array(errors), np.array(matched_times), np.array(matched_expected)


def estimate_bpm(onsets):
    if len(onsets) < 4:
        return None
    ibi = np.diff(onsets)
    ibi = ibi[(ibi > 0.2) & (ibi < 1.5)]
    if len(ibi) == 0:
        return None
    hist, edges = np.histogram(ibi, bins=np.arange(0.2, 1.5, 0.005))
    return 60.0 / edges[np.argmax(hist)]


# --------------------------------------------------------------------------
# YIN monophonic pitch tracker (FFT autocorrelation, vectorized inner loop)
# --------------------------------------------------------------------------
def yin_pitches(y, sr, hop=256, win=2048, fmin=60, fmax=1500, thr=0.15):
    nfft = 1 << (win * 2 - 1).bit_length()
    tau_min = max(1, int(sr / fmax))
    tau_max = int(sr / fmin)
    taus = np.arange(tau_max + 1)
    times, midis = [], []
    for s in range(0, len(y) - win, hop):
        x = y[s:s + win].astype(np.float64)
        times.append((s + win / 2) / sr)
        if np.sqrt(np.mean(x * x)) < 1e-3:
            midis.append(np.nan)
            continue
        x -= x.mean()
        spec = np.fft.rfft(x, nfft)
        ac = np.fft.irfft(np.abs(spec) ** 2, nfft)[:tau_max + 1]
        d = 2 * (ac[0] - ac)
        d[0] = 0.0
        cum = np.cumsum(d)
        cmnd = np.where(taus > 0, d * taus / np.maximum(cum, 1e-12), 1.0)
        region = cmnd[tau_min:tau_max + 1]
        k = int(np.argmin(region))
        if region[k] >= thr:
            midis.append(np.nan)
            continue
        tau = float(tau_min + k)
        if tau_min <= tau - 1 and tau + 1 <= tau_max:
            a, b, c = cmnd[int(tau) - 1], cmnd[int(tau)], cmnd[int(tau) + 1]
            denom = a - 2 * b + c
            if denom != 0:
                tau += 0.5 * (a - c) / denom
        midis.append(69 + 12 * np.log2(sr / tau / 440))
    return np.array(times), np.array(midis)


def pitch_accuracy(times, midis, notes, bpm):
    spb = 60.0 / bpm
    active = [(n.pitch, n.start * spb, (n.start + n.duration) * spb) for n in notes]
    voiced = np.isfinite(midis)
    ratio = float(voiced.mean()) if len(voiced) else 0.0
    if voiced.sum() == 0:
        return dict(voiced_ratio=ratio, pitch_accuracy=None, octave_accuracy=None,
                    median_error_semitones=None)
    ok, ok_oct, errs = 0, 0, []
    for t, m in zip(times[voiced], midis[voiced]):
        expected = {p for p, a, b in active if a - 0.05 <= t < b + 0.05}
        if not expected:
            continue
        nearest = min(expected, key=lambda p: abs(p - m))
        err = m - nearest
        errs.append(err)
        if abs(err) < 0.5:
            ok += 1
        folded = abs(err) % 12
        if min(folded, 12 - folded) < 0.5:
            ok_oct += 1
    n = len(errs)
    return dict(voiced_ratio=ratio, pitch_accuracy=ok / n if n else None,
                octave_accuracy=ok_oct / n if n else None,
                median_error_semitones=float(np.median(np.abs(errs))) if errs else None,
                evaluated_frames=n)


# --------------------------------------------------------------------------
# chroma / harmony
# --------------------------------------------------------------------------
def chroma_profile(y, sr, segments):
    """segments: [(t0, t1, label)] -> [{label: 12-bin chroma array}]"""
    win, hop = 4096, int(sr * 0.01)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    bins = np.round(69 + 12 * np.log2(freqs[1:] / 440)).astype(int) % 12
    out = []
    for t0, t1, label in segments:
        seg = y[int(t0 * sr):int(t1 * sr)]
        nfr = max(0, (len(seg) - win) // hop)
        if nfr == 0:
            out.append((label, None))
            continue
        idx = np.arange(win)[None, :] + np.arange(nfr)[:, None] * hop
        mag = np.abs(np.fft.rfft(seg[idx] * np.hanning(win), axis=1))
        m = mag[:, 1:].sum(axis=0)
        chroma = np.zeros(12)
        np.add.at(chroma, bins, m)
        out.append((label, chroma))
    return out


def harmony_concentration(segments, expected_pcs):
    rows = []
    for (label, chroma), pcs in zip(segments, expected_pcs):
        if chroma is None or chroma.sum() == 0:
            rows.append(dict(segment=label, concentration=None))
            continue
        rows.append(dict(segment=label,
                         concentration=float(chroma[list(pcs)].sum() / chroma.sum()),
                         top3=[int(i) for i in np.argsort(chroma)[::-1][:3]]))
    return rows


# --------------------------------------------------------------------------
# swing / groove phase
# --------------------------------------------------------------------------
def observed_offbeat_phases(onsets, notes, bpm, offbeat_positions):
    """Mean observed phase-within-beat of onsets expected at offbeat positions."""
    spb = 60.0 / bpm
    exp = [n.start * spb for n in notes
           if any(abs((n.start % 1) - p) < 1e-6 for p in offbeat_positions)]
    if not exp:
        return dict(n=None, mean_phase_beats=None, std_phase_beats=None)
    errors, times, _ = match_onsets(onsets, exp, window_s=0.06)
    if len(times) == 0:
        return dict(n=0, mean_phase_beats=None, std_phase_beats=None)
    phases = (times / spb) % 1
    return dict(n=len(times), mean_phase_beats=float(np.mean(phases)),
                std_phase_beats=float(np.std(phases)),
                matched_errors_ms=dict(
                    median=float(np.median(np.abs(errors)) * 1000),
                    p95=float(np.percentile(np.abs(errors), 95) * 1000)))


# --------------------------------------------------------------------------
# per-fixture analysis
# --------------------------------------------------------------------------
def analyze(wav_path, notes, bpm, kind):
    y, sr = sf.read(wav_path, always_2d=True)
    y = np.mean(y, axis=1)
    spb = 60.0 / bpm
    onsets = detect_onsets(y, sr)
    expected = [n.start * spb for n in notes]
    errors, _, _ = match_onsets(onsets, expected)
    out = dict(
        expected_onsets=len(expected),
        detected_onsets=len(onsets),
        matched_onsets=len(errors),
        onset_error_ms_median=float(np.median(np.abs(errors)) * 1000) if len(errors) else None,
        onset_error_ms_p95=float(np.percentile(np.abs(errors), 95) * 1000) if len(errors) else None,
        onset_error_ms_mean_signed=float(np.mean(errors) * 1000) if len(errors) else None,
        bpm_estimate=estimate_bpm(onsets),
        kind=kind,
    )
    if kind in ('a', 'd'):
        times, midis = yin_pitches(y, sr)
        out['pitch'] = pitch_accuracy(times, midis, notes, bpm)
    if kind == 'd':
        first, last = [], []
        for i, n in enumerate(notes):
            t = n.start * spb
            if t < 10:
                first.append(t)
            elif t >= 50:
                last.append(t)
        for label, exp in (('first10s', first), ('last10s', last)):
            err, _, _ = match_onsets(onsets, exp)
            out[f'onset_error_{label}_ms'] = float(np.median(np.abs(err)) * 1000) if len(err) else None
        if len(errors) >= 4:
            tt, ee = match_onsets(onsets, expected)[1:]
            slope = np.polyfit(ee, tt, 1)[0]
            out['drift_rate_ms_per_s'] = float((slope - 1) * 1000)
    if kind == 'b':
        chords = (('C', (60, 64, 67)), ('Am', (57, 60, 64)), ('F', (53, 57, 60)), ('G', (55, 59, 62)))
        segments = [(i * 5, (i + 1) * 5, label) for i, (label, _) in enumerate(chords)]
        expected_pcs = [[p % 12 for p in pitches] for _, pitches in chords]
        out['harmony'] = harmony_concentration(chroma_profile(y, sr, segments), expected_pcs)
    if kind in ('c-straight', 'c-swing'):
        pos = (0.25, 0.75) if kind == 'c-straight' else (1 / 3, 5 / 6)
        out['offbeat'] = observed_offbeat_phases(onsets, notes, bpm, pos)
    return out


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2))
