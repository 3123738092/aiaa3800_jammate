"""Real-time jam: microphone in, band out (sounddevice / PortAudio).

Use headphones: the phrase detector listens to the microphone and would
otherwise hear the band. The band is rendered one bar ahead, so decisions
apply to the next unrendered bar (one bar is 2.5 s at 96 BPM). Type band
directions ("faster", "更有力", "your turn") in the console; `q` quits.

Scheduler and capture paths are tested without a device; actual sound-card
latency and microphone recognition still require manual validation.
"""

from __future__ import annotations

from collections import deque
import logging
import queue
import random
import threading
import time
from pathlib import Path

import numpy as np

from jammate.analysis.pitch import segment_notes, to_beats, track_pitch
from jammate.analysis.rhythm import analyze_rhythm_bar
from jammate.analysis.rhythm_capture import RhythmCapture
from jammate.analysis.vad import VadConfig, VadState, level_db, vad_step
from jammate.director.commands import parse_command
from jammate.director.policy import (Decision, Event, PolicyConfig, Silence, UserCommand,
                                     UserPhrase, UserRhythm, decide)
from jammate.generate.arranger import arrange_bar, place_lead
from jammate.music import BEATS_PER_BAR, Note
from jammate.render.synth import SR, render
from jammate.render.timeline import AudioTimeline
from jammate.render.clock import BarClock, BarTiming
from jammate.rhythm import RhythmContext, RhythmMode, load_rhythm
from jammate.session_log import SessionLog, decision_record
from jammate.state import JamState

LOG = logging.getLogger("jammate.live")
BLOCK = 1024
SCHEDULER_TICK_S = 0.05
PRE_ROLL_SAMPLES = int(0.5 * SR)
MIC_QUEUE_BLOCKS = int(30 * SR / BLOCK)  # if analysis stalls, keep only the last ~30 s


class LiveJam:
    """Owns the jam state; three threads share it under `_lock`.

    - sound-card callbacks: read the timeline / push microphone blocks;
    - scheduler thread: renders the next bar and invites after silence;
    - listener thread: detects sung phrases or collects rhythm bars.
    """

    def __init__(self, state: JamState, seed: int, log: SessionLog,
                 config: PolicyConfig = PolicyConfig(), vad_config: VadConfig = VadConfig(),
                 input_mode: str = "hum") -> None:
        if input_mode not in {"hum", "rhythm"}:
            raise ValueError("input_mode must be hum or rhythm")
        self._input_mode = input_mode
        self._state = state
        self._rng = random.Random(seed)
        self._log = log
        self._config = config
        self._vad_config = vad_config
        self._lock = threading.Lock()
        self._leads: dict[int, tuple[Note, ...]] = {}
        self._next_bar = 0
        self._next_bar_sample = PRE_ROLL_SAMPLES
        self._clock = BarClock(SR, PRE_ROLL_SAMPLES)
        self._bar_timings: deque[BarTiming] = deque(maxlen=64)
        self._playback_pending: deque[BarTiming] = deque()
        self._playback_ticks: queue.Queue[tuple[int, int, float, int]] = queue.Queue(maxsize=128)
        self._quiet_bars = 0
        self._timeline = AudioTimeline()
        self._mic: queue.Queue[tuple[int, np.ndarray]] = queue.Queue(maxsize=MIC_QUEUE_BLOCKS)
        self._stop = threading.Event()

    # -- director (call with _lock held) --------------------------------------
    def _decide_locked(self, event: Event) -> Decision:
        decision = decide(self._state, event, self._rng, self._config)
        self._state = decision.state
        if decision.lead:
            # A new idea replaces whatever was still queued, so motifs never splice.
            self._leads = place_lead(decision.lead, self._next_bar)
            self._quiet_bars = 0
        return decision

    def _report(self, decision: Decision) -> None:
        self._log.write("decision", **decision_record(decision))
        LOG.info("[%s] %s", decision.action.value, decision.reason)

    def apply(self, event: Event) -> None:
        received = time.monotonic_ns()
        with self._lock:
            decision = self._decide_locked(event)
            bar, sample = self._next_bar, self._next_bar_sample
            if isinstance(event, (UserPhrase, UserRhythm)):
                self._quiet_bars = 0
        self._log.write("decision", **decision_record(decision), received_monotonic_ns=received,
                        effective_bar=bar, effective_sample=sample)
        LOG.info("[%s] %s", decision.action.value, decision.reason)

    # -- band -----------------------------------------------------------------
    def _render_next_bar(self) -> None:
        invitation = None
        with self._lock:
            skipped = self._clock.catch_up(self._timeline.read_position)
            if skipped:
                LOG.warning("skipped %d missed bars to preserve the beat grid", skipped)
            self._next_bar = self._clock.bar_index
            self._next_bar_sample = self._clock.next_sample
            self._leads = {i: notes for i, notes in self._leads.items() if i >= self._next_bar}
            lead_pending = self._next_bar <= max(self._leads, default=-1)
            if self._input_mode == "hum" and not lead_pending and self._quiet_bars >= self._config.initiate_after_bars:
                invitation = self._decide_locked(Silence(self._quiet_bars))
            bar, state = self._next_bar, self._state
            lead = self._leads.pop(bar, ())
            notes = tuple(n.shifted(-bar * BEATS_PER_BAR) for n in arrange_bar(state, bar, lead))
            timing = self._clock.reserve(state.tempo_bpm)
            start = timing.start_sample
            self._next_bar_sample = self._clock.next_sample
            self._next_bar = self._clock.bar_index
            self._bar_timings.append(timing)
            self._playback_pending.append(timing)
            self._quiet_bars = 0 if lead else self._quiet_bars + 1
        if invitation is not None:
            self._report(invitation)
        if notes:
            self._timeline.add(start, render(notes, state.tempo_bpm))
        self._log.write("bar_scheduled", bar=timing.bar_index, start_sample=start,
                        end_sample=timing.end_sample, tempo_bpm=state.tempo_bpm,
                        rhythm_mode=state.rhythm_mode.value, rhythm=state.rhythm.to_dict() if state.rhythm else None,
                        render_finished_monotonic_ns=time.monotonic_ns(),
                        output_read_sample=self._timeline.read_position)

    def _log_playback(self) -> None:
        # Disk I/O belongs to the worker, never to the sound-card callback.
        while True:
            try:
                first, frames, dac_time, callback_ns = self._playback_ticks.get_nowait()
            except queue.Empty:
                return
            while self._playback_pending and self._playback_pending[0].start_sample < first + frames:
                timing = self._playback_pending.popleft()
                if timing.start_sample < first:
                    self._log.write("playback_unobserved", bar=timing.bar_index)
                    continue
                device_time = dac_time + (timing.start_sample - first) / SR
                self._log.write("bar_playback", bar=timing.bar_index,
                                start_sample=timing.start_sample, device_dac_time_s=device_time,
                                callback_monotonic_ns=callback_ns)

    def _scheduler(self) -> None:
        while not self._stop.wait(SCHEDULER_TICK_S):
            try:
                self._log_playback()
                with self._lock:
                    ahead = self._next_bar_sample - self._timeline.read_position
                    bar_samples = BEATS_PER_BAR * 60.0 / self._state.tempo_bpm * SR
                if ahead < bar_samples:
                    self._render_next_bar()
            except Exception:  # keep the band playing; log and try again next tick
                LOG.exception("scheduler error")

    # -- listening ------------------------------------------------------------
    def _listener(self) -> None:
        vad, blocks = VadState(), []
        while not self._stop.is_set():
            try:
                _, block = self._mic.get(timeout=0.1)
            except queue.Empty:
                continue
            blocks.append(block)
            vad, phrase = vad_step(vad, level_db(block), self._vad_config)
            if phrase is None:
                if not vad.in_phrase and len(blocks) > self._vad_config.end_silence_blocks:
                    blocks = blocks[-self._vad_config.end_silence_blocks:]
                continue
            first = vad.block - len(blocks)  # absolute index of blocks[0]
            audio = np.concatenate(blocks[phrase[0] - first:phrase[1] - first])
            blocks = blocks[phrase[1] - first:]
            try:
                self._on_phrase(audio)
            except Exception:  # one bad phrase must not stop the listening
                LOG.exception("could not analyse phrase")

    def _on_phrase(self, audio: np.ndarray) -> None:
        with self._lock:
            tempo = self._state.tempo_bpm
        timed = segment_notes(*track_pitch(audio, SR))
        notes = to_beats(timed, tempo, origin_s=timed[0].start_s) if timed else ()
        self._log.write("phrase", notes=[[n.pitch, n.start, n.duration] for n in notes])
        if notes:
            self.apply(UserPhrase(notes))

    def _rhythm_listener(self) -> None:
        capture = RhythmCapture()
        while not self._stop.is_set():
            try:
                start, block = self._mic.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                timings = tuple(self._bar_timings)
            for timing, audio in capture.feed(start, block, timings):
                try:
                    context = analyze_rhythm_bar(audio, SR, timing.tempo_bpm,
                                                  beat_origin_s=timing.start_sample / SR)
                    self._log.write("rhythm_input", captured_bar=timing.bar_index,
                                    input_end_sample=timing.end_sample, context=context.to_dict())
                    # These onsets were measured against a known playback
                    # tempo, not an independent estimate of a new tempo.
                    self.apply(UserRhythm(context, update_tempo=False))
                except Exception:
                    LOG.exception("could not analyse rhythm bar")

    # -- sound card -------------------------------------------------------------
    def _output_callback(self, outdata, frames, _time, status) -> None:  # noqa: ANN001
        if status:
            LOG.debug("output status: %s", status)
        # Bars are limited one by one; overlapping release tails can still add up.
        start = self._timeline.read_position
        outdata[:, 0] = np.clip(self._timeline.read(frames), -1.0, 1.0)
        try:
            self._playback_ticks.put_nowait((start, frames, float(_time.outputBufferDacTime), time.monotonic_ns()))
        except queue.Full:
            pass  # missing observations are logged by the worker as unobserved

    def _input_callback(self, indata, _frames, _time, status) -> None:  # noqa: ANN001
        if status:
            LOG.debug("input status: %s", status)
        block = indata[:, 0].copy()
        # A duplex PortAudio stream provides ADC and DAC timestamps on the
        # same clock. Map captured samples onto the band's output timeline.
        sample = self._timeline.read_position + round(
            (float(_time.inputBufferAdcTime) - float(_time.outputBufferDacTime)) * SR)
        item = (sample, block)
        try:
            self._mic.put_nowait(item)
        except queue.Full:  # analysis is behind: drop the oldest audio, keep the newest
            try:
                self._mic.get_nowait()
            except queue.Empty:
                pass
            self._mic.put_nowait(item)

    def _duplex_callback(self, indata, outdata, frames, timing, status) -> None:  # noqa: ANN001
        self._input_callback(indata, frames, timing, status)
        self._output_callback(outdata, frames, timing, status)

    def run(self) -> None:
        import sounddevice as sd

        workers = [threading.Thread(target=self._scheduler, daemon=True),
                   threading.Thread(target=self._rhythm_listener if self._input_mode == "rhythm"
                                    else self._listener, daemon=True)]
        try:
            self._render_next_bar()  # render before the device starts consuming samples
            with sd.Stream(samplerate=SR, channels=1, blocksize=BLOCK, dtype="float32",
                           callback=self._duplex_callback):
                for worker in workers:
                    worker.start()
                LOG.info("jamming at %.0f BPM in %s (input: %s). Type a direction; q quits.",
                         self._state.tempo_bpm, self._state.key.name, self._input_mode)
                self._console()
        finally:
            self._stop.set()
            for worker in workers:
                if worker.ident is not None:
                    worker.join(timeout=2)
            self._log_playback()

    def _console(self) -> None:
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if line.lower() in {"q", "quit", "exit"}:
                return
            if line.lower().startswith("rhythm "):
                try:
                    context = load_rhythm(line[7:].strip().strip('"'))
                    self.apply(UserRhythm(context))
                except (OSError, ValueError) as exc:
                    LOG.error("could not load rhythm: %s", exc)
                continue
            intents = parse_command(line)
            self._log.write("command", text=line, intents=[i.value for i in intents])
            if intents:
                self.apply(UserCommand(intents, source="text"))
            else:
                LOG.info("(didn't catch a direction in %r)", line)


def run_live(tempo_bpm: float = 96.0, seed: int = 0, log_path: Path | None = None,
             rhythm: RhythmContext | None = None, rhythm_mode: RhythmMode = RhythmMode.FOLLOW,
             input_mode: str = "hum") -> int:
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        LOG.error("live mode needs sounddevice and PortAudio: %s", exc)
        return 1
    try:
        state = JamState(rhythm_mode=rhythm_mode).with_tempo(tempo_bpm)
        if rhythm is not None:
            decision = decide(state, UserRhythm(rhythm), random.Random(seed))
            if decision.action.value == "hold":
                raise ValueError(decision.reason)
            state = decision.state
        LiveJam(state, seed, SessionLog(log_path), input_mode=input_mode).run()
    except (sd.PortAudioError, ValueError) as exc:  # no device, unsupported sample rate, ...
        LOG.error("audio device error: %s", exc)
        return 1
    except KeyboardInterrupt:
        LOG.info("stopped")
    return 0
