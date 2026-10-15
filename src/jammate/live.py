"""Real-time jam: microphone in, band out (sounddevice / PortAudio).

Use headphones: the phrase detector listens to the microphone and would
otherwise hear the band. The band is rendered one bar ahead, so a decision
is heard from the next bar boundary (one bar is 2.5 s at 96 BPM). Type band
directions ("faster", "更有力", "your turn") in the console; `q` quits.

Thin adapter over the sound card: exercised by hand, not by unit tests.
"""

from __future__ import annotations

import logging
import queue
import random
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

from jammate.analysis.pitch import segment_notes, to_beats, track_pitch
from jammate.analysis.vad import VadConfig, VadState, level_db, vad_step
from jammate.director.commands import parse_command
from jammate.director.policy import (Decision, Event, PolicyConfig, Silence, UserCommand,
                                     UserPhrase, decide)
from jammate.generate.arranger import arrange_bar, place_lead
from jammate.music import BEATS_PER_BAR, Note
from jammate.render.synth import SR, render
from jammate.render.timeline import AudioTimeline
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
    - listener thread: detects sung phrases and asks the director to answer.
    """

    def __init__(self, state: JamState, seed: int, log: SessionLog,
                 config: PolicyConfig = PolicyConfig(), vad_config: VadConfig = VadConfig()) -> None:
        self._state = state
        self._rng = random.Random(seed)
        self._log = log
        self._config = config
        self._vad_config = vad_config
        self._lock = threading.Lock()
        self._leads: dict[int, tuple[Note, ...]] = {}
        self._next_bar = 0
        self._next_bar_sample = PRE_ROLL_SAMPLES
        self._quiet_bars = 0
        self._timeline = AudioTimeline()
        self._mic: queue.Queue[np.ndarray] = queue.Queue(maxsize=MIC_QUEUE_BLOCKS)
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
        with self._lock:
            decision = self._decide_locked(event)
        self._report(decision)

    # -- band -----------------------------------------------------------------
    def _render_next_bar(self) -> None:
        invitation = None
        with self._lock:
            lead_pending = self._next_bar <= max(self._leads, default=-1)
            if not lead_pending and self._quiet_bars >= self._config.initiate_after_bars:
                invitation = self._decide_locked(Silence(self._quiet_bars))
            bar, state = self._next_bar, self._state
            lead = self._leads.pop(bar, ())
            notes = tuple(n.shifted(-bar * BEATS_PER_BAR) for n in arrange_bar(state, bar, lead))
            read_position = self._timeline.read_position
            if self._next_bar_sample < read_position:
                # Rendering fell behind playback: skip ahead rather than play
                # truncated bars back to back.
                LOG.warning("band fell behind by %d samples; resyncing",
                            read_position - self._next_bar_sample)
                self._next_bar_sample = read_position + BLOCK
            start = self._next_bar_sample
            self._next_bar_sample += int(round(BEATS_PER_BAR * 60.0 / state.tempo_bpm * SR))
            self._next_bar += 1
            self._quiet_bars = 0 if lead else self._quiet_bars + 1
        if invitation is not None:
            self._report(invitation)
        if notes:
            self._timeline.add(start, render(notes, state.tempo_bpm))

    def _scheduler(self) -> None:
        while not self._stop.wait(SCHEDULER_TICK_S):
            try:
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
                block = self._mic.get(timeout=0.1)
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

    # -- sound card -------------------------------------------------------------
    def _output_callback(self, outdata, frames, _time, status) -> None:  # noqa: ANN001
        if status:
            LOG.debug("output status: %s", status)
        # Bars are limited one by one; overlapping release tails can still add up.
        outdata[:, 0] = np.clip(self._timeline.read(frames), -1.0, 1.0)

    def _input_callback(self, indata, _frames, _time, status) -> None:  # noqa: ANN001
        if status:
            LOG.debug("input status: %s", status)
        block = indata[:, 0].copy()
        try:
            self._mic.put_nowait(block)
        except queue.Full:  # analysis is behind: drop the oldest audio, keep the newest
            try:
                self._mic.get_nowait()
            except queue.Empty:
                pass
            self._mic.put_nowait(block)

    def run(self) -> None:
        workers = [threading.Thread(target=self._scheduler, daemon=True),
                   threading.Thread(target=self._listener, daemon=True)]
        try:
            with sd.OutputStream(samplerate=SR, channels=1, blocksize=BLOCK, dtype="float32",
                                 callback=self._output_callback), \
                 sd.InputStream(samplerate=SR, channels=1, blocksize=BLOCK, dtype="float32",
                                callback=self._input_callback):
                for worker in workers:
                    worker.start()
                LOG.info("jamming at %.0f BPM in %s. Hum a phrase, or type a direction; q quits.",
                         self._state.tempo_bpm, self._state.key.name)
                self._console()
        finally:
            self._stop.set()

    def _console(self) -> None:
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if line.lower() in {"q", "quit", "exit"}:
                return
            intents = parse_command(line)
            self._log.write("command", text=line, intents=[i.value for i in intents])
            if intents:
                self.apply(UserCommand(intents, source="text"))
            else:
                LOG.info("(didn't catch a direction in %r)", line)


def run_live(tempo_bpm: float = 96.0, seed: int = 0, log_path: Path | None = None) -> int:
    try:
        LiveJam(JamState().with_tempo(tempo_bpm), seed, SessionLog(log_path)).run()
    except (sd.PortAudioError, ValueError) as exc:  # no device, unsupported sample rate, ...
        LOG.error("audio device error: %s", exc)
        return 1
    except KeyboardInterrupt:
        LOG.info("stopped")
    return 0
