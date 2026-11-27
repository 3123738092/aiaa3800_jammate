"""Incremental PCM rendering for the JamMate browser's scheduled bars."""

import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock, local
from time import monotonic

import numpy as np

from jammate.music import BEATS_PER_BAR
from jammate.orchestration.backend_arrangement import MRTArrangement
from jammate.render.mrt2_style import english_style

from .inference import (
    DRUM_KEY, FRAME_SAMPLES, PITCH_KEY, SAMPLE_RATE, STYLE_KEY, create_mlxfn_model, encode_style, generate_frame,
)
from .plan_conditioning import band_plan_style

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

    def __init__(self, prompt, model, *, direct=False, tempo=False, key=False):
        self.prompt, self.model = english_style(prompt) if prompt else "", model
        self.direct = direct
        self.explicit_tempo, self.explicit_key = tempo, key
        self._encoded_prompt = None
        self._engine = self._style = self._state = None
        self._tail = b""
        self._next_bar = 0
        self._closed = Event()
        self._rendering = Lock()
        self._report_lock = Lock()
        self._reports = deque(maxlen=12)
        self._generated_frames = 0
        self._late_frames = 0

    def close(self):
        self._closed.set()

    def diagnostics(self):
        with self._report_lock:
            return {"recent_bars": list(self._reports), "generated_frames": self._generated_frames,
                    "frames_over_40ms": self._late_frames}

    @staticmethod
    def samples_for_bar(timing, clock_rate):
        return (round(timing.end_sample * SAMPLE_RATE / clock_rate)
                - round(timing.start_sample * SAMPLE_RATE / clock_rate))

    @contextmanager
    def open_bar(self, bar, clock_rate, *, reference=None):
        """Claim one ordered stream. A broken response ends this model timeline."""
        if not self._rendering.acquire(timeout=.25):
            raise ValueError("上一小节的 MRT2 音频仍在发送。")
        try:
            if self._closed.is_set() or bar.timing.bar_index != self._next_bar:
                raise ValueError("这个 MRT2 音频小节已失效，请重新开始。")
            samples = self.samples_for_bar(bar.timing, clock_rate)
            try:
                yield samples * SAMPLE_BYTES, self._chunks(bar, samples, reference)
            except BaseException:
                self.close()
                raise
            self._next_bar += 1
        finally:
            self._rendering.release()

    def _chunks(self, bar, samples, reference):
        received = monotonic()
        if self.direct:
            if bar.plan is not None:
                self.explicit_tempo |= "tempo_bpm" in bar.plan.to_dict()["transport"]
                self.explicit_key |= any(action["action"] == "SET_KEY" for action in bar.plan.actions)
            prompt = band_plan_style(self.prompt, bar.state, bar.plan, tempo=self.explicit_tempo,
                                     key=self.explicit_key)
        else:
            prompt = self.prompt if bar.notes else None
        active = prompt is not None or reference is not None
        source = ("audio", reference[0]) if reference else ("text", prompt)
        change = active and source != self._encoded_prompt
        if change:
            _mlx_worker.submit(self._prepare_model, source, reference).result()
        ready = monotonic()
        if not active:
            # Pauses and cold openings without material produce exact silence.
            if self._state is not None:
                _mlx_worker.submit(self._reset_model_state).result()
            self._tail = b""
        carry = len(self._tail) // SAMPLE_BYTES
        count = math.ceil((samples - carry) / FRAME_SAMPLES)
        if active and not self.direct:
            notes, drums = bar_conditions(bar, count, carry)
        start = monotonic()
        remaining = samples
        elapsed_frames = []
        report = {"bar": bar.timing.bar_index, "plan_id": bar.plan.plan_id if bar.plan else None,
                  "style_prompt": None if reference else prompt, "style_tokens": self._style if active else None,
                  "audio_ref_event": reference[0] if reference else None,
                  "style_reencoded": change, "condition_ready_ms": round((ready - received) * 1000, 3),
                  "conditioning": ("audio_style" if reference else "silence" if not active
                                   else "text" if self.direct else "text_pitch_drum"),
                  "first_model_pcm_ms": None}
        if active and not self.direct:
            report["grid"] = {"frame_rate": SAMPLE_RATE // FRAME_SAMPLES, "frames": count,
                              "pitch_cells": int(np.count_nonzero(notes)), "drum_frames": int(np.count_nonzero(drums))}
        if self._tail:
            yield self._tail
            remaining -= carry
            self._tail = b""
        for frame in range(count):
            elapsed = (samples - remaining) / SAMPLE_RATE
            if self._closed.wait(max(0., start + elapsed - STREAM_LEAD_S - monotonic())):
                return
            if active:
                conditioning = {STYLE_KEY: self._style}
                if not self.direct:
                    conditioning.update({PITCH_KEY: notes[frame].tolist(), DRUM_KEY: drums[frame].tolist()})
                frame_start = monotonic()
                block = _mlx_worker.submit(self._render, conditioning).result()
                if block is None:
                    return
                elapsed_frames.append(monotonic() - frame_start)
                if report["first_model_pcm_ms"] is None:
                    report["first_model_pcm_ms"] = round((monotonic() - received) * 1000, 3)
            else:
                block = bytes(FRAME_SAMPLES * SAMPLE_BYTES)
            take = min(remaining, FRAME_SAMPLES)
            size = take * SAMPLE_BYTES
            self._tail = block[size:] if active else b""
            remaining -= take
            yield block[:size]
        late_frames = sum(time > .04 for time in elapsed_frames)
        report.update(frames=len(elapsed_frames), frames_over_40ms=late_frames,
                      frame_p95_ms=round(float(np.percentile(elapsed_frames, 95)) * 1000, 3) if elapsed_frames else None)
        with self._report_lock:
            self._reports.append(report)
            self._generated_frames += len(elapsed_frames)
            self._late_frames += late_frames


    def _prepare_model(self, source, reference):
        if self._engine is None:
            self._engine = _load_stream_model(self.model)
        if reference:
            from magenta_rt.audio import Waveform

            _, audio, rate = reference
            value = Waveform(samples=audio[:, None], sample_rate=rate)
        else:
            value = source[1]
        self._style = encode_style(self._engine, value)
        self._encoded_prompt = source

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
    beats_per_bar = bar.state.beats_per_bar if bar.state is not None else BEATS_PER_BAR
    bar_beat = timing.bar_index * beats_per_bar
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
