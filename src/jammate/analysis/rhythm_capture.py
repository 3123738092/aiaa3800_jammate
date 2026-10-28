"""Collect complete microphone bars on the output sample clock.

Missing/dropped blocks invalidate that bar. They must never be concatenated
as though no time elapsed, which would shift all following rhythm attacks.
"""

from __future__ import annotations

import numpy as np

from jammate.render.clock import BarTiming


class RhythmCapture:
    def __init__(self) -> None:
        self._partial: dict[int, tuple[int, list[np.ndarray]]] = {}

    def feed(self, start_sample: int, audio: np.ndarray,
             timings: tuple[BarTiming, ...]) -> list[tuple[BarTiming, np.ndarray]]:
        end_sample = start_sample + len(audio)
        known = {t.bar_index for t in timings if t.end_sample > start_sample}
        self._partial = {i: p for i, p in self._partial.items() if i in known}
        completed = []
        for timing in timings:
            left = max(start_sample, timing.start_sample)
            right = min(end_sample, timing.end_sample)
            if left >= right:
                continue
            expected, pieces = self._partial.get(timing.bar_index, (timing.start_sample, []))
            if left != expected:
                self._partial.pop(timing.bar_index, None)
                continue
            pieces.append(audio[left - start_sample:right - start_sample].copy())
            if right == timing.end_sample:
                completed.append((timing, np.concatenate(pieces)))
                self._partial.pop(timing.bar_index, None)
            else:
                self._partial[timing.bar_index] = (right, pieces)
        return completed
