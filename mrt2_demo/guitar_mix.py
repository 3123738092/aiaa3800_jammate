"""Highlight a scheduled clean electric guitar riff in MRT2's stereo mix."""

from functools import lru_cache

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import istft, stft

from .inference import SAMPLE_RATE


GUITAR = "gm_27"
_WINDOW, _HOP, _FFT = 4096, 512, 8192
_BLOCK = 40 * _HOP
_PAD = _WINDOW + 6 * _HOP
_EDGE = round(.018 * SAMPLE_RATE)
_BAND = np.fft.rfftfreq(_FFT, 1 / SAMPLE_RATE)
_BAND = _BAND[_BAND <= 6500]
_SAMPLE_BYTES = 4


def guitar_focus(bar):
    """Return the requested boost and pitched spans, or None for the original mix."""
    state = bar.state
    if not state or not state.playing:
        return None
    layer = next((part for part in state.layers if part.name == GUITAR), None)
    if layer is None or not layer.active or not layer.density or layer.level <= .5:
        return None
    guitar = sorted((note for note in bar.notes if note.instrument == GUITAR), key=lambda n: n.start)
    if not guitar or any(left.end > right.start + 1e-9 for left, right in zip(guitar, guitar[1:])):
        return None
    # Accent a monophonic riff while protecting all other pitched notes in the score.
    offset = bar.timing.bar_index * state.beats_per_bar
    seconds_per_beat = 60 / bar.timing.tempo_bpm
    spans = [(n.instrument == GUITAR, n.pitch, (n.start - offset) * seconds_per_beat + .04,
              (n.end - offset) * seconds_per_beat + .04)
             for n in bar.notes if n.instrument != "drums"]
    return min(6., (layer.level - .5) * 30), spans


@lru_cache(maxsize=128)
def _harmonics(pitch):
    hz = 440 * 2 ** ((pitch - 69) / 12)
    peaks = np.arange(1, min(16, int(6500 / hz)) + 1) * hz
    if not peaks.size:
        return np.zeros(_BAND.shape, dtype=np.float32)
    distance = np.min(np.abs(_BAND[:, None] - peaks[None, :]), axis=1)
    return np.exp(-.5 * (distance / (12 + _BAND * .003)) ** 2).astype(np.float32)


def _correction(pcm, offset, spans, boost):
    if len(pcm) < _WINDOW:
        return np.zeros_like(pcm, dtype=np.float32)
    _, times, spectrum = stft(pcm.T.astype(np.float32), SAMPLE_RATE, nperseg=_WINDOW,
                              noverlap=_WINDOW - _HOP, nfft=_FFT)
    times += offset / SAMPLE_RATE
    band = spectrum[:, :_BAND.size]
    target = np.zeros(band.shape[1:], dtype=np.float32)
    protected = np.zeros_like(target)
    for focused, pitch, onset, end in spans:
        gate = np.clip(np.minimum((times - onset + .014) / .04, (end + .085 - times) / .08), 0, 1)
        indices = np.flatnonzero(gate > 0)
        if indices.size:
            weights = target if focused else protected
            weights[:, indices] = np.maximum(weights[:, indices], _harmonics(pitch)[:, None] * gate[indices])
    if not np.any(target):
        return np.zeros_like(pcm, dtype=np.float32)
    power = np.mean(np.abs(band) ** 2, axis=0)
    sustained = median_filter(power, size=(1, 13), mode="nearest")
    transient = median_filter(power, size=(17, 1), mode="nearest")
    tonality = sustained / (sustained + 2 * transient + 1e-15)
    band *= (10 ** (boost / 20) - 1) * target * (1 - protected) * tonality
    spectrum[:, _BAND.size:] = 0
    _, delta = istft(spectrum, SAMPLE_RATE, nperseg=_WINDOW, noverlap=_WINDOW - _HOP, nfft=_FFT)
    return delta.T[:len(pcm)]


def focus_chunks(chunks, samples, focus):
    """Render bounded blocks with analysis context on both sides of each join."""
    boost, spans = focus
    iterator = iter(chunks)
    pending = bytearray()
    past = b""
    for offset in range(0, samples, _BLOCK):
        count = min(_BLOCK, samples - offset)
        required = min(samples - offset, count + _PAD) * _SAMPLE_BYTES
        while len(pending) < required:
            block = next(iterator, None)
            if block is None:
                return
            pending.extend(block)
        window = np.frombuffer(past + pending[:required], dtype="<i2").reshape(-1, 2)
        back = len(past) // _SAMPLE_BYTES
        dry = window[back:back + count]
        delta = _correction(window, offset - back, spans, boost)[back:back + count]
        # Leave the original samples at bar edges and protect transient headroom.
        positions = np.arange(offset, offset + count)
        taper = np.minimum(1, np.minimum(positions, samples - positions - 1) / _EDGE)
        scale = np.clip((.98 * 32768 - np.abs(dry.astype(np.float32))) / (.18 * 32768), 0, 1)
        mixed = np.clip(np.rint(dry + delta * taper[:, None] * scale), -32768, 32767)
        past = (past + pending[:count * _SAMPLE_BYTES])[-_PAD * _SAMPLE_BYTES:]
        del pending[:count * _SAMPLE_BYTES]
        yield mixed.astype("<i2").tobytes()
    # Let the generator publish the final model frame timings after yielding the final bytes.
    next(iterator, None)
