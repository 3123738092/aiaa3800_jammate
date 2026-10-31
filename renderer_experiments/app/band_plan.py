"""Exact BandPlan execution through Kevin's MIDI / FluidSynth backend.

Offline export only: successful receipts prove artifact generation, not playback.
The session arranger is authoritative; MusicIntent/pattern defaults are not used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

import numpy as np
import soundfile as sf

from jammate.contracts import PlanError
from jammate.orchestration.offline import prepare_plan, RenderedPlan
from .midi.events import MidiEvent
from .midi.files import write_midi, write_events, render_with_fluidsynth
from .presets import get_instrument

CHANNELS = {"drums": 9, "bass": 2, "keys": 1, "lead": 4}


def midi_events(performance):
    """Session beats -> segment seconds, retaining source rests and velocities."""
    events = []
    origin = performance.start_bar * 4
    scale = 60 / performance.tempo_bpm
    for note in performance.notes:
        channel = CHANNELS[note.instrument]
        events.extend((MidiEvent((note.start - origin) * scale, "note_on", channel, note.pitch, note.velocity),
                       MidiEvent((note.end - origin) * scale, "note_off", channel, note.pitch, 0)))
    return sorted(events, key=lambda event: (event.timestamp, event.type == "note_on"))


def verify_midi(path, expected, programs, duration_s):
    """Independently decode the saved file; never validate only source arrays."""
    import mido
    mid = mido.MidiFile(path)
    seconds, actual, found_programs = 0., [], {}
    tempo = None
    for message in mid:
        seconds += message.time
        if message.type == "set_tempo":
            tempo = message.tempo
        elif message.type == "program_change":
            found_programs[message.channel] = message.program
        elif message.type in {"note_on", "note_off"}:
            actual.append((seconds, message.type, message.channel, message.note, message.velocity))
    if tempo is None:
        raise ValueError("missing MIDI tempo")
    tolerance = tempo / 1e6 / mid.ticks_per_beat / 2 + 1e-8
    ordered = sorted(expected, key=lambda e: (round(mido.second2tick(e.timestamp, mid.ticks_per_beat, tempo)), e.type == "note_on"))
    wanted = [(e.timestamp, e.type, e.channel, e.note, e.velocity) for e in ordered
              if e.type in {"note_on", "note_off"}]
    if len(actual) != len(wanted) or found_programs != programs:
        raise ValueError("MIDI event count or instrument programs changed")
    error = 0.
    for a, b in zip(actual, wanted):
        error = max(error, abs(a[0] - b[0]))
        if a[1:] != b[1:]:
            raise ValueError("MIDI pitch/channel/velocity changed")
    if error > tolerance or abs(seconds - duration_s) > tolerance:
        raise ValueError("MIDI timing or trailing rest changed")
    return {"note_count": len(wanted) // 2, "max_time_error_s": error,
            "tolerance_s": tolerance, "duration_s": seconds, "programs": found_programs}


def _write(path, value):
    # An interrupted write must not leave a half JSON receipt.
    fd, temporary = tempfile.mkstemp(prefix=".jammate-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class KevinRuntime:
    """Versioned execution receipt after verified MIDI and SoundFont WAV export.

    One directory per execution; a failed/stale result remains diagnostic evidence
    and cannot mutate JamState. No program, file name or backend comes from LLM text.
    """
    def __init__(self, output: Path, *, executable: Path, soundfont: Path,
                 profile="electric", lead_program=73, sample_rate=44100, seed=3800):
        self.output = Path(output)
        self.executable, self.soundfont = Path(executable), Path(soundfont)
        self.programs = {k: v for k, v in get_instrument(profile).programs.items() if k in (1, 2)}
        if isinstance(lead_program, bool) or not isinstance(lead_program, int) or not 0 <= lead_program <= 127:
            raise ValueError("lead_program must be an integer within 0-127")
        if not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 96000:
            raise ValueError("sample_rate must be within 8000-96000")
        self.programs[4] = lead_program
        self.profile, self.sample_rate, self.seed = profile, sample_rate, seed
        self.last_artifacts = None
        self.last_receipt = None
        self._execution_lock = threading.Lock()

    def validate(self, state, plan):
        performance = prepare_plan(state, plan, seed=self.seed)
        # Reject ambiguous same-pitch overlaps on one channel rather than silently
        # shortening one note with another note-off. Polyphonic different pitches work.
        import mido
        tempo = mido.bpm2tempo(performance.tempo_bpm)
        tick = lambda beat: round(mido.second2tick((beat - performance.start_bar * 4) * 60 / performance.tempo_bpm, 480, tempo))
        ends = {}
        for note in sorted(performance.notes, key=lambda n: n.start):
            key = (CHANNELS[note.instrument], note.pitch)
            # Upstream seconds are decimal-rounded: adjacent notes can overlap
            # by one microsecond. Judge the actual MIDI ticks, not float noise.
            if tick(note.start) < ends.get(key, -1):
                raise PlanError("MIDI backend cannot preserve overlapping same-pitch voices on one channel")
            if tick(note.end) <= tick(note.start):
                raise PlanError("note duration is shorter than MIDI tick resolution")
            ends[key] = tick(note.end)
        return performance

    def execute(self, manager, plan):
        # One worker per runtime instance. New input can still invalidate the
        # manager's pending plan while this worker is rendering.
        with self._execution_lock:
            return self._execute(manager, plan)

    def _execute(self, manager, plan):
        state = manager.check_pending(plan)
        data = plan.to_dict()
        bounds = {"effective_bar": data["effective_at"]["bar"],
                  "completed_bar": data["effective_at"]["bar"] + data["valid_for_bars"]}
        digest = hashlib.sha256((state.session_id + ":" + plan.plan_id).encode()).hexdigest()[:20]
        folder = self.output / f"plan_{digest}"
        self.last_artifacts = folder
        self.last_receipt = None
        receipt = {"schema_version": "1.0.0", "session_id": state.session_id, "plan_id": plan.plan_id,
                   "based_on_state_version": state.state_version, **bounds,
                   "backend": "kevin_midi_fluidsynth", "mode": "offline_export", "succeeded": False}
        # A colliding directory may belong to another active exporter. Refuse
        # overwrite without failing that exporter's plan in the shared manager.
        folder.mkdir(parents=True, exist_ok=False)
        try:
            plan.write(folder / "plan.json")
            _write(folder / "state_observed.json", state.to_dict())
            performance = self.validate(state, plan)
            events = midi_events(performance)
            duration = performance.bars * 4 * 60 / performance.tempo_bpm
            write_events(folder / "events.json", events)
            write_midi(folder / "band.mid", events, performance.tempo_bpm, self.programs, duration_s=duration)
            verification = verify_midi(folder / "band.mid", events, self.programs, duration)
            render_with_fluidsynth(folder / "band.mid", folder / "band.wav", self.executable,
                                  self.soundfont, self.sample_rate)
            audio, sr = sf.read(folder / "band.wav", dtype="float32", always_2d=True)
            if sr != self.sample_rate or len(audio) < round(duration * sr) - 2 or not np.isfinite(audio).all():
                raise ValueError("invalid WAV duration, sample rate or samples")
            rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
            if performance.notes and rms < 1e-7:
                raise ValueError("renderer produced silence for a nonempty performance")
            receipt.update(midi_verification=verification, wav={"sample_rate": sr, "channels": audio.shape[1],
                           "duration_s": len(audio) / sr, "rms": rms,
                           "peak": float(np.max(np.abs(audio))) if audio.size else 0.},
                           instrument_profile=self.profile, lead_program=self.programs[4],
                           soundfont_sha256=hashlib.sha256(self.soundfont.read_bytes()).hexdigest())
            # Save generated artifacts before acknowledging; a cancelled plan must
            # not become an applied state even if its offline export finished.
            _write(folder / "render_result.json", receipt)
            accepted, committed = manager.acknowledge_and_snapshot(plan, succeeded=True, lead_notes=performance.lead_notes,
                                                                 generated_motif=performance.generated_motif, **bounds)
            if not accepted:
                raise PlanError("execution receipt was not accepted; plan already reached a terminal status")
            receipt.update(succeeded=True, state_version_after=committed.state_version,
                           manager_diagnostics=manager.diagnostics(), persistence_warnings=[])
            # Commit already happened. Report persistence failure separately;
            # never throw an apparent execution failure that invites replay.
            for name, value in (("state_applied.json", committed.to_dict()), ("receipt.json", receipt)):
                try:
                    _write(folder / name, value)
                except OSError as exc:
                    receipt["persistence_warnings"].append({"file": name, "error": type(exc).__name__, "errno": exc.errno})
            self.last_receipt = receipt
            return RenderedPlan(performance.notes, performance.lead_notes, performance.generated_motif,
                                performance.tempo_bpm, performance.start_bar, performance.bars, audio, sr)
        except Exception as exc:
            receipt.update(reason=type(exc).__name__ + ": " + str(exc), plan_status=manager.plan_status(plan.plan_id))
            if manager.plan_status(plan.plan_id) == "pending":
                manager.acknowledge(plan, succeeded=False, reason=receipt["reason"], **bounds)
            self.last_receipt = receipt
            try:
                _write(folder / "receipt.json", receipt)
            except OSError as write_error:
                receipt["persistence_warnings"] = [{"file": "receipt.json", "error": type(write_error).__name__, "errno": write_error.errno}]
            raise
