"""Incremental PCM rendering for the JamMate browser's scheduled bars."""

import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from threading import Condition, Event, Lock, local
from time import monotonic

import numpy as np

from jammate.instruments import gm_program
from jammate.music import BEATS_PER_BAR
from jammate.orchestration.backend_arrangement import MRTArrangement
from jammate.render.mrt2_style import english_style

from .guitar_mix import focus_chunks, guitar_focus
from .inference import (
    DRUM_KEY, FRAME_SAMPLES, PITCH_KEY, SAMPLE_RATE, STYLE_KEY, create_mlxfn_model, encode_style, generate_frame,
)
from .plan_conditioning import band_plan_style, score_stream_style

CHANNELS = 2
SAMPLE_BYTES = CHANNELS * 2
STREAM_LEAD_S = .85
SCORE_STREAM_LEAD_S = 2.6
ENCODED_REPLAY_BARS = 6
BRIDGE_FRAMES = round(.006 * SAMPLE_RATE)
# MLX streams are thread-local. HTTP handlers can change between bars.
_mlx_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jam-mrt2-mlx")
_mlx_cache = local()


def _load_stream_model(size):
    if getattr(_mlx_cache, "size", None) != size:
        _mlx_cache.model = create_mlxfn_model(size)
        _mlx_cache.size = size
    return _mlx_cache.model


def _bridge_start(block, previous_sample):
    pcm = np.frombuffer(block, dtype="<i2").reshape(-1, CHANNELS).copy()
    edge = np.frombuffer(previous_sample, dtype="<i2")
    fade = (.5 - .5 * np.cos(np.linspace(0., math.pi, BRIDGE_FRAMES)))[:, None]
    pcm[:BRIDGE_FRAMES] = np.rint(edge + fade * (pcm[:BRIDGE_FRAMES].astype(float) - edge))
    return pcm.tobytes()


class BrowserMRT2Stream:
    """Own the model context and fractional frame tail for one listening session."""

    def __init__(self, prompt, model, *, direct=False, tempo=False, key=False, lead_voice=None):
        self.prompt, self.model = english_style(prompt) if prompt else "", model
        self.direct = direct
        self.explicit_tempo, self.explicit_key = tempo, key
        self.lead_voice = lead_voice
        self._encoded_prompt = None
        self._engine = self._style = self._state = None
        self._tail = b""
        self._last_sample = None
        self._previous_score = None
        self._backing = None
        self._guitar_riff = False
        self._held_pitches = {}
        self._buffered_bars = {}
        self._next_bar = 0
        self._closed = Event()
        self._rendering = Condition()
        self._report_lock = Lock()
        self._reports = deque(maxlen=12)
        self._generated_frames = 0
        self._late_frames = 0
        self._replayed_bars = 0

    def close(self):
        self._closed.set()
        # A renderer that owns the lock also wakes its waiters when it exits.
        if self._rendering.acquire(blocking=False):
            try:
                self._rendering.notify_all()
            finally:
                self._rendering.release()

    def _continue_score(self, state, layers, prompt):
        previous = self._previous_score
        names = {layer.name for layer in layers}
        settings = (layers, state.tempo_bpm, state.beats_per_bar, state.key, state.chord_cycle,
                    state.progression_variant, state.rhythm, state.texture, prompt)
        self._previous_score = (names, settings)
        if previous is None or self._state is None:
            return False
        previous_names, previous_settings = previous
        added = names - previous_names
        if previous_names and previous_names < names and len(added) == 1:
            guitar, = added
            program = gm_program(guitar)
            if program is not None and 26 <= program <= 31:
                self._backing = (guitar, previous_settings, self._state)
        if self._backing and self._backing[0] not in names:
            _, saved_settings, saved_state = self._backing
            self._backing = None
            if settings == saved_settings:
                self._state = saved_state
                return True
        return False

    def diagnostics(self):
        with self._report_lock:
            return {"recent_bars": list(self._reports), "generated_frames": self._generated_frames,
                    "frames_over_40ms": self._late_frames, "replayed_bars": self._replayed_bars}

    @staticmethod
    def samples_for_bar(timing, clock_rate):
        return (round(timing.end_sample * SAMPLE_RATE / clock_rate)
                - round(timing.start_sample * SAMPLE_RATE / clock_rate))

    @contextmanager
    def open_bar(self, bar, clock_rate, *, reference=None, replayable=False):
        """Serve compressed-response sources independently of model execution."""
        if replayable:
            raw = self._prepare_bar(bar, clock_rate, reference)
            yield len(raw), (chunk for chunk in (raw,))
            return
        if not self._rendering.acquire(timeout=.25):
            raise ValueError("上一小节的 MRT2 音频仍在发送。")
        try:
            if self._closed.is_set() or bar.timing.bar_index != self._next_bar:
                raise ValueError("这个 MRT2 音频小节已失效，请重新开始。")
            samples = self.samples_for_bar(bar.timing, clock_rate)
            try:
                yield samples * SAMPLE_BYTES, self._audio_chunks(bar, samples, reference)
            except BaseException:
                self.close()
                raise
            self._next_bar += 1
        finally:
            self._rendering.notify_all()
            self._rendering.release()

    def _prepare_bar(self, bar, clock_rate, reference):
        timeout = 8.
        deadline = monotonic() + timeout
        if not self._rendering.acquire(timeout=timeout):
            raise TimeoutError("这个 MRT2 小节仍在生成，请重试。")
        try:
            index = bar.timing.bar_index
            if not self._rendering.wait_for(lambda: self._closed.is_set() or index <= self._next_bar,
                                            timeout=max(0., deadline - monotonic())):
                raise TimeoutError("前面的小节尚未生成，请重试。")
            if self._closed.is_set():
                raise ValueError("这个 MRT2 会话已经结束。")
            if index in self._buffered_bars:
                with self._report_lock:
                    self._replayed_bars += 1
                return self._buffered_bars[index]
            if index != self._next_bar:
                raise ValueError("这个 MRT2 音频小节已失效，请重新开始。")
            samples = self.samples_for_bar(bar.timing, clock_rate)
            try:
                raw = b"".join(self._audio_chunks(bar, samples, reference))
                if len(raw) != samples * SAMPLE_BYTES:
                    raise ValueError("MRT2 小节未生成完整。")
            except BaseException:
                self.close()
                raise
            self._buffered_bars[index] = raw
            if len(self._buffered_bars) > ENCODED_REPLAY_BARS:
                self._buffered_bars.pop(next(iter(self._buffered_bars)))
            self._next_bar += 1
            return raw
        finally:
            self._rendering.notify_all()
            self._rendering.release()

    def _audio_chunks(self, bar, samples, reference):
        focus = guitar_focus(bar) if not self.direct and reference is None else None
        with closing(self._chunks(bar, samples, reference)) as chunks:
            if focus is None:
                yield from chunks
                return
            yield from focus_chunks(chunks, samples, focus)
        with self._report_lock:
            if self._reports and self._reports[-1]["bar"] == bar.timing.bar_index:
                self._reports[-1]["guitar_focus_db"] = round(focus[0], 2)

    def _chunks(self, bar, samples, reference):
        received = monotonic()
        if self.direct:
            if bar.plan is not None:
                self.explicit_tempo |= "tempo_bpm" in bar.plan.to_dict()["transport"]
                self.explicit_key |= any(action["action"] == "SET_KEY" for action in bar.plan.actions)
            prompt = band_plan_style(self.prompt, bar.state, bar.plan, tempo=self.explicit_tempo,
                                     key=self.explicit_key)
        else:
            layers = tuple(layer for layer in bar.state.layers
                           if bar.state.playing and layer.active and layer.level > 0 and layer.density > 0)
            audible = {layer.name for layer in layers}
            self._held_pitches = {key: length for key, length in self._held_pitches.items() if key[0] in audible}
            if audible != {"drums", "bass", "gm_27"}:
                self._guitar_riff = False
            else:
                guitar = sorted((note for note in bar.notes if note.instrument == "gm_27"), key=lambda note: note.start)
                if len(guitar) >= 2:
                    self._guitar_riff = (len({note.pitch for note in guitar}) >= 2
                                         and all(left.end <= right.start + 1e-9 for left, right in zip(guitar, guitar[1:])))
            prompt = (score_stream_style(bar.state.style or self.prompt, bar.state, lead_voice=self.lead_voice,
                                         guitar_riff=self._guitar_riff)
                      if bar.notes or self._held_pitches else None)
        active = prompt is not None or reference is not None
        restored = False
        if active and not self.direct and not reference:
            restored = self._continue_score(bar.state, layers, prompt)
        else:
            self._previous_score = self._backing = None
            self._guitar_riff = False
        boundary_sample = self._tail[-SAMPLE_BYTES:] or self._last_sample
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
            notes, drums, held_pitches = bar_conditions(bar, count, carry, samples, self._held_pitches)
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
            report["backing_context_restored"] = restored
            report["grid"] = {"frame_rate": SAMPLE_RATE // FRAME_SAMPLES, "frames": count,
                              "pitch_cells": int(np.count_nonzero(notes)), "drum_frames": int(np.count_nonzero(drums)),
                              "held_pitches": len(self._held_pitches)}
            report["score_layers"] = {part: sum(note.instrument == part for note in bar.notes)
                                      for part in sorted({note.instrument for note in bar.notes})}
            self._held_pitches = held_pitches
        if self._tail:
            yield self._tail
            remaining -= carry
            self._tail = b""
        lead = STREAM_LEAD_S if self.direct else SCORE_STREAM_LEAD_S
        for frame in range(count):
            elapsed = (samples - remaining) / SAMPLE_RATE
            if self._closed.wait(max(0., start + elapsed - lead - monotonic())):
                return
            if active:
                conditioning = {STYLE_KEY: self._style}
                if not self.direct:
                    conditioning.update({PITCH_KEY: notes[frame].tolist(), DRUM_KEY: drums[frame].tolist()})
                frame_start = monotonic()
                block = _mlx_worker.submit(self._render, conditioning).result()
                if block is None:
                    return
                if restored and frame == 0 and boundary_sample is not None:
                    block = _bridge_start(block, boundary_sample)
                elapsed_frames.append(monotonic() - frame_start)
                if report["first_model_pcm_ms"] is None:
                    report["first_model_pcm_ms"] = round((monotonic() - received) * 1000, 3)
            else:
                block = bytes(FRAME_SAMPLES * SAMPLE_BYTES)
            take = min(remaining, FRAME_SAMPLES)
            size = take * SAMPLE_BYTES
            self._tail = block[size:] if active else b""
            remaining -= take
            if not remaining:
                self._last_sample = block[size - SAMPLE_BYTES:size]
            yield block[:size]
        late_frames = sum(time > .04 for time in elapsed_frames)
        report.update(frames=len(elapsed_frames), frames_over_40ms=late_frames,
                      generation_wall_ms=round((monotonic() - received) * 1000, 3),
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


def bar_conditions(bar, frames, carry, samples, held=None):
    """Project clip-relative notes on the 25 Hz grid without restarting that grid."""
    timing = bar.timing
    beats_per_bar = bar.state.beats_per_bar if bar.state is not None else BEATS_PER_BAR
    bar_beat = timing.bar_index * beats_per_bar
    samples_per_beat = SAMPLE_RATE * 60 / timing.tempo_bpm
    pitches, drums, next_held = [], [], {}
    held = held or {}
    lead = sorted((note for note in bar.notes if note.instrument == "lead"), key=lambda note: note.start)
    next_lead_start = {note: next_note.start for note, next_note in zip(lead, lead[1:])
                       if note.end <= next_note.start + 1e-9}
    for note in bar.notes:
        onset = ((note.start - bar_beat) * samples_per_beat - carry) / FRAME_SAMPLES
        end_samples = (note.end - bar_beat) * samples_per_beat
        offset = (end_samples - carry) / FRAME_SAMPLES
        start = min(frames - 1, max(0, math.floor(onset + 1e-9)))
        end = min(frames, max(start + 1, math.ceil(offset - 1e-9)))
        if note in next_lead_start:
            next_onset = ((next_lead_start[note] - bar_beat) * samples_per_beat - carry) / FRAME_SAMPLES
            end = min(end, max(start + 1, math.floor(next_onset + 1e-9)))
        span = {"frame": start, "length": end - start}
        if note.instrument == "drums":
            drums.append(span)
        else:
            pitches.append({**span, "pitch": note.pitch})
            if end_samples > samples + .5:
                key = (note.instrument, note.pitch)
                next_held[key] = max(next_held.get(key, 0), end_samples - samples)
    notes, drum_tokens = MRTArrangement("", tuple(pitches), tuple(drums)).tokens(frames)
    first_lead = (lead[0].start - bar_beat) * samples_per_beat if lead else None
    for key, length in held.items():
        instrument, pitch = key
        stop = math.ceil((length - carry) / FRAME_SAMPLES - 1e-9)
        if instrument == "lead" and first_lead is not None and length <= first_lead + 1e-9:
            stop = min(stop, math.floor((first_lead - carry) / FRAME_SAMPLES + 1e-9))
        # A held note uses the sustain token. Keep explicit new attacks or other voices on the same pitch.
        cells = notes[:max(0, stop), pitch]
        np.maximum(cells, 1, out=cells)
        if length > samples + .5:
            next_held[key] = max(next_held.get(key, 0), length - samples)
    return notes, drum_tokens, next_held
