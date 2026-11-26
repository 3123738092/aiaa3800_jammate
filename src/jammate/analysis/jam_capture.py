"""Local routing and InputEvent creation for a continuous Jam microphone."""
from uuid import uuid4

import numpy as np
from scipy.signal import correlate, find_peaks, resample_poly

from jammate.analysis.pitch import segment_notes, track_pitch
from jammate.analysis.rhythm import rhythm_from_onsets
from jammate.contracts.adapters import from_humming, from_rhythm

INPUT_MODES = {"auto", "speech", "rhythm", "humming"}


def _activity(audio, rate):
    hop = max(1, rate // 100)
    frames = audio[:len(audio) // hop * hop].reshape(-1, hop)
    if not len(frames):
        return (), hop
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    audible = rms >= max(.007, float(rms.max()) * .075)
    edges = np.flatnonzero(np.diff(np.r_[False, audible, False]))
    regions = []
    for start, end in zip(edges[::2], edges[1::2]):
        if regions and start - regions[-1][1] < 4:
            previous, _, peak = regions[-1]
            regions[-1] = (previous, end, max(peak, float(rms[start:end].max())))
        else:
            regions.append((start, end, float(rms[start:end].max())))
    return tuple(regions), hop


def isolated_attack_times(audio, rate):
    """Return onset times when the recording consists of several isolated short sounds."""
    regions, hop = _activity(audio, rate)
    if len(regions) < 3 or any((end - start) * hop / rate > .10 for start, end, _ in regions):
        return []
    return [round(int(start) * hop / rate, 2) for start, _, _ in regions]


def _likely_hum(audio, rate, regions):
    """Look for a series of sustained, clearly periodic pitches before transcription."""
    if not regions or sum(end - start for start, end, _ in regions) < 44:
        return False
    probe_rate = 8000
    divisor = np.gcd(rate, probe_rate)
    probe = resample_poly(audio, probe_rate // divisor, rate // divisor)
    frame = 400
    stable = []
    previous = None
    count = 0
    for start in range(0, max(0, len(probe) - frame + 1), 160):
        chunk = probe[start:start + frame]
        chunk = chunk - chunk.mean()
        pitch = None
        if float(np.sqrt(np.mean(chunk ** 2))) >= .009:
            corr = correlate(chunk, chunk, mode="full", method="direct")[frame - 1:]
            peaks, _ = find_peaks(corr[:124])
            peaks = [lag for lag in peaks if 10 <= lag and corr[lag] >= .78 * corr[0] * (1 - lag / frame)]
            if peaks:
                pitch = 69 + 12 * np.log2(probe_rate / peaks[0] / 440)
        if pitch is not None and previous is not None and abs(pitch - previous) < .38:
            count += 1
        else:
            if count >= 8:
                stable.append(previous)
            previous, count = pitch, 1
    if count >= 8:
        stable.append(previous)
    return len(stable) >= 2 and max(stable) - min(stable) >= 1.0


def route_capture(audio, rate, *, mode, session_id, bpm):
    """Recognize local rhythm or route a recording to melody or speech analysis."""
    regions, hop = _activity(audio, rate)
    if not regions:
        return None, None
    duration = len(audio) / rate
    # A short, separated word can have three voiced bursts while none of them
    # reaches the sustained-region threshold on its own.
    activity_s = sum(end - start for start, end, _ in regions) * hop / rate
    speech_like = activity_s >= .11
    if mode in {"rhythm", "auto"}:
        transients = [region for region in regions if (region[1] - region[0]) * hop / rate <= .17]
        if mode == "rhythm" or (len(transients) >= 2 and len(regions) == len(transients)):
            attacks = [start * hop / rate for start, _, _ in transients]
            if len(attacks) >= 2 and duration + 1e-9 >= attacks[0] + 240 / bpm:
                peak = max(peak for _, _, peak in transients)
                accents = [strength / peak for _, _, strength in transients]
                context = rhythm_from_onsets(attacks, bpm, attacks[0], accents)
                if len(context.onset_beats) >= 2 and context.confidence >= .65:
                    return from_rhythm(context, session_id=session_id, audio_duration_s=duration,
                                       tempo_source="session", confidence_kind="grid_alignment_heuristic"), None
            return None, "speech" if mode == "auto" and speech_like else None

    if mode == "humming" or (mode == "auto" and _likely_hum(audio, rate, regions)):
        return None, "humming"
    return None, "speech" if mode in {"speech", "auto"} and speech_like else None


def analyze_capture(audio, rate, *, mode, session_id, bpm, allow_no_notes=False):
    """Analyze audio-only requests, including those from older Jam clients."""
    event, route = route_capture(audio, rate, mode=mode, session_id=session_id, bpm=bpm)
    if event is not None or route != "humming":
        return event, route == "speech"
    duration = len(audio) / rate

    probe_rate = 22050
    divisor = np.gcd(rate, probe_rate)
    pitch_audio = resample_poly(audio, probe_rate // divisor, rate // divisor)
    notes = []
    for timed in segment_notes(*track_pitch(pitch_audio, probe_rate), min_duration=.14):
        end = min(duration, timed.end_s)
        if end <= timed.start_s:
            continue
        identity = f"n{len(notes) + 1}"
        notes.append({"id": identity, "pitchMidi": timed.pitch, "startSec": timed.start_s,
                      "durationSec": end - timed.start_s, "velocity01": .7, "sourceNoteIds": [identity]})
    if (mode == "humming" and (notes or allow_no_notes)) or len({note["pitchMidi"] for note in notes}) >= 2:
        source = uuid4().hex
        document = {"schemaVersion": "1.0.0", "id": source, "status": "ready" if notes else "no_notes",
                    "source": {"id": source, "durationSec": duration, "kind": "recording"},
                    "timebase": {"unit": "seconds", "origin": "original_audio_start"},
                    "tempo": {"bpm": None, "source": "unknown", "mode": "free", "quantized": False},
                    "key": None, "timeSignature": None, "selectedResult": "cleaned", "notes": notes,
                    "model": {"name": "librosa.pyin"},
                    "results": {"raw": {"notes": notes}, "cleaned": {"notes": notes}}}
        return from_humming(document, session_id=session_id), False
    return None, mode == "auto"
