"""Adapt the unchanged upstream recognizer to a free recording timeline."""

from __future__ import annotations

import numpy as np

from jammate.analysis import rhythm


def analyze_free_rhythm(audio: np.ndarray, sample_rate: int) -> tuple[list[float], list[float]]:
    """Return upstream onset seconds/accents without count-in or grid wrapping.

    Keep the historical float32 channel mean and delegate every recognition
    decision to rhythm._onsets_and_accents, including its original librosa
    settings, padding/backtracking, amplitude gates and 60 ms peak strengths.

    InputEvent requires unique times. Only exactly equal backtracked timestamps
    are consolidated, retaining their maximum accent. Distinct close times are
    never snapped, merged or otherwise refined.
    """
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim == 2:
        samples = np.mean(samples, axis=1)
    onsets, accents = rhythm._onsets_and_accents(samples, sample_rate)
    by_time: dict[float, float] = {}
    for onset, accent in zip(onsets, accents):
        when, strength = float(onset), float(accent)
        by_time[when] = max(by_time.get(when, strength), strength)
    times = sorted(by_time)
    return times, [by_time[when] for when in times]
