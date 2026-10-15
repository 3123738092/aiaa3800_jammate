"""Overlap-add buffer between the bar renderer and the audio callback.

Writers add rendered audio at absolute sample positions (so release tails of
one bar overlap the next); the sound-card callback reads it in order. This is
inherently stateful, so it is a small lock-protected class.
"""

from __future__ import annotations

import threading

import numpy as np


class AudioTimeline:
    def __init__(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._origin = 0  # absolute sample index of _buffer[0]
        self._lock = threading.Lock()

    @property
    def read_position(self) -> int:
        with self._lock:
            return self._origin

    @property
    def buffered_until(self) -> int:
        """Absolute sample index up to which audio has been written."""
        with self._lock:
            return self._origin + self._buffer.size

    def add(self, start: int, wave: np.ndarray) -> None:
        with self._lock:
            skip = max(0, self._origin - start)
            wave = np.asarray(wave, dtype=np.float32)[skip:]
            offset = start + skip - self._origin
            needed = offset + wave.size
            if needed > self._buffer.size:
                self._buffer = np.concatenate(
                    (self._buffer, np.zeros(needed - self._buffer.size, dtype=np.float32)))
            self._buffer[offset:needed] += wave

    def read(self, n: int) -> np.ndarray:
        with self._lock:
            out = np.zeros(n, dtype=np.float32)
            take = min(n, self._buffer.size)
            out[:take] = self._buffer[:take]
            self._buffer = self._buffer[take:]
            self._origin += n
            return out
