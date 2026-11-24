"""Incremental PCM rendering for the JamMate browser's scheduled bars."""

import math
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock, local
from time import monotonic

import numpy as np

from jammate.music import BEATS_PER_BAR
from jammate.orchestration.backend_arrangement import MRTArrangement

from .inference import (
    DRUM_KEY, FRAME_SAMPLES, PITCH_KEY, SAMPLE_RATE, STYLE_KEY, create_mlxfn_model, encode_style, generate_frame,
)

CHANNELS = 2
SAMPLE_BYTES = CHANNELS * 2
STREAM_LEAD_S = .20
# MLX streams are thread-local. HTTP handlers can change between bars.
_mlx_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jam-mrt2-mlx")
_mlx_cache = local()


def _load_stream_model(size):
    if getattr(_mlx_cache, "size", None) != size:
        _mlx_cache.model = create_mlxfn_model(size)
        _mlx_cache.size = size
    return _mlx_cache.model


class BrowserMRT2Stream:
    """Own the model context and fractional frame tail for one listening session."""

    def __init__(self, prompt, model):
        self.prompt, self.model = prompt, model
        self._engine = self._style = self._state = None
        self._tail = b""
        self._next_bar = 0
        self._closed = Event()
        self._rendering = Lock()

    def close(self):
        self._closed.set()

    @staticmethod
    def samples_for_bar(timing, clock_rate):
        return (round(timing.end_sample * SAMPLE_RATE / clock_rate)
                - round(timing.start_sample * SAMPLE_RATE / clock_rate))

    @contextmanager
    def open_bar(self, bar, clock_rate):
        """Claim one ordered stream. A broken response ends this model timeline."""
        if not self._rendering.acquire(timeout=.25):
            raise ValueError("上一小节的 MRT2 音频仍在发送。")
        try:
            if self._closed.is_set() or bar.timing.bar_index != self._next_bar:
                raise ValueError("这个 MRT2 音频小节已失效，请重新开始。")
            samples = self.samples_for_bar(bar.timing, clock_rate)
            try:
                yield samples * SAMPLE_BYTES, self._chunks(bar, samples)
            except BaseException:
                self.close()
                raise
            self._next_bar += 1
        finally:
            self._rendering.release()

    def _chunks(self, bar, samples):
        if bar.notes and self._engine is None:
            _mlx_worker.submit(self._prepare_model).result()
        if not bar.notes:
            # Pauses and cold openings without material produce exact silence.
            if self._state is not None:
                _mlx_worker.submit(self._reset_model_state).result()
            self._tail = b""
        carry = len(self._tail) // SAMPLE_BYTES
        count = math.ceil((samples - carry) / FRAME_SAMPLES)
        if bar.notes:
            notes, drums = bar_conditions(bar, count, carry)
        start = monotonic()
        remaining = samples
        if self._tail:
            yield self._tail
            remaining -= carry
            self._tail = b""
        for frame in range(count):
            elapsed = (samples - remaining) / SAMPLE_RATE
            if self._closed.wait(max(0., start + elapsed - STREAM_LEAD_S - monotonic())):
                return
            if bar.notes:
                conditioning = {STYLE_KEY: self._style, PITCH_KEY: notes[frame].tolist(),
                                DRUM_KEY: drums[frame].tolist()}
                block = _mlx_worker.submit(self._render, conditioning).result()
                if block is None:
                    return
            else:
                block = bytes(FRAME_SAMPLES * SAMPLE_BYTES)
            take = min(remaining, FRAME_SAMPLES)
            size = take * SAMPLE_BYTES
            self._tail = block[size:] if bar.notes else b""
            remaining -= take
            yield block[:size]


    def _prepare_model(self):
        self._engine = _load_stream_model(self.model)
        self._style = encode_style(self._engine, self.prompt)

    def _reset_model_state(self):
        self._state = None

    def _render(self, conditioning):
        if self._closed.is_set():
            return None
        pcm, self._state = generate_frame(self._engine, conditioning, self._state)
        return (np.clip(pcm, -1., 32767 / 32768) * 32768).astype("<i2").tobytes()


def bar_conditions(bar, frames, carry):
    """Project clip-relative notes on the 25 Hz grid without restarting that grid."""
    timing = bar.timing
    bar_beat = timing.bar_index * BEATS_PER_BAR
    samples_per_beat = SAMPLE_RATE * 60 / timing.tempo_bpm
    pitches, drums = [], []
    for note in bar.notes:
        onset = ((note.start - bar_beat) * samples_per_beat - carry) / FRAME_SAMPLES
        offset = ((note.end - bar_beat) * samples_per_beat - carry) / FRAME_SAMPLES
        start = min(frames - 1, max(0, math.floor(onset + 1e-9)))
        end = min(frames, max(start + 1, math.ceil(offset - 1e-9)))
        span = {"frame": start, "length": end - start}
        if note.instrument == "drums":
            drums.append(span)
        else:
            pitches.append({**span, "pitch": note.pitch})
    return MRTArrangement("", tuple(pitches), tuple(drums)).tokens(frames)
