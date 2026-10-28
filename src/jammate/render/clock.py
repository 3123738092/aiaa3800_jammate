"""Sample-clock bar boundaries shared by scheduling and rhythm capture."""

from __future__ import annotations

import math
from dataclasses import dataclass

from jammate.music import BEATS_PER_BAR


@dataclass(frozen=True)
class BarTiming:
    bar_index: int
    start_sample: int
    end_sample: int
    tempo_bpm: float


class BarClock:
    """Accumulate fractional samples so rounding error does not grow per bar."""

    def __init__(self, sr: int, start_sample: int = 0) -> None:
        if sr <= 0 or start_sample < 0:
            raise ValueError("sample rate must be positive and start_sample nonnegative")
        self.sr = sr
        self._next_sample = float(start_sample)
        self.bar_index = 0
        self._last_tempo: float | None = None

    @property
    def next_sample(self) -> int:
        return round(self._next_sample)

    def reserve(self, tempo_bpm: float) -> BarTiming:
        if not math.isfinite(tempo_bpm) or tempo_bpm <= 0:
            raise ValueError("tempo must be finite and positive")
        start = self.next_sample
        self._next_sample += BEATS_PER_BAR * 60.0 / tempo_bpm * self.sr
        timing = BarTiming(self.bar_index, start, self.next_sample, tempo_bpm)
        self.bar_index += 1
        self._last_tempo = tempo_bpm
        return timing

    def catch_up(self, read_sample: int) -> int:
        """Skip missed whole bars, preserving the old phase before a tempo update."""
        skipped = 0
        if self._last_tempo is None:
            return skipped
        while self.next_sample < read_sample:
            self.reserve(self._last_tempo)
            skipped += 1
        return skipped

    def schedule(self, tempo_bpm: float, read_sample: int = 0) -> BarTiming:
        if not math.isfinite(tempo_bpm) or tempo_bpm <= 0:
            raise ValueError("tempo must be finite and positive")
        if self._last_tempo is None:
            self._last_tempo = tempo_bpm
        self.catch_up(read_sample)
        timing = self.reserve(tempo_bpm)
        return timing
