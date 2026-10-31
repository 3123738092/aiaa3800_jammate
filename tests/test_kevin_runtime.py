"""Artifact/receipt boundaries, independent MIDI decoding, no audio device."""
from pathlib import Path
import sys

import mido
import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderer_experiments.app import band_plan as backend
from renderer_experiments.app.midi.events import MidiEvent
from renderer_experiments.app.midi.files import write_midi
from jammate.contracts import PlanError
from jammate.contracts.adapters import from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.manager import StateManager
from jammate.orchestration.plans import make_plan
from jammate.orchestration.offline import PlannedPerformance
from jammate.music import Note
from jammate.state import JamState


def staged():
    manager = StateManager(JamState(session_id="kevin_test", tempo_bpm=103.7))
    events = manager.ingest([from_text("鼓更猛", session_id="kevin_test", event_id="e1")])
    plan = RuleAgent().decide(events, manager.state, plan_id="p1")
    manager.stage(plan)
    return manager, plan


def runtime(tmp_path):
    font = tmp_path / "mock.sf2"
    font.write_bytes(b"mock-soundfont-not-used")
    return backend.KevinRuntime(tmp_path / "outputs", executable=tmp_path / "mock.exe", soundfont=font)


def fake_render(midi, wav, executable, soundfont, sample_rate):
    duration = mido.MidiFile(midi).length
    t = np.arange(round((duration + .1) * sample_rate)) / sample_rate
    sf.write(wav, np.repeat((.01 * np.sin(t * 1000))[:, None], 2, axis=1), sample_rate)


def test_absolute_ticks_prevent_long_unquantized_timing_drift_and_keep_rest(tmp_path):
    events = []
    for i in range(300):
        start = .371 + i * .037123
        events.extend([MidiEvent(start, "note_on", 4, 60, 70), MidiEvent(start + .02123, "note_off", 4, 60, 0)])
    path = tmp_path / "exact.mid"
    write_midi(path, events, 103.7, {4: 73}, duration_s=13.)
    result = backend.verify_midi(path, events, {4: 73}, 13.)
    assert result["note_count"] == 300
    assert result["max_time_error_s"] <= result["tolerance_s"]


def test_all_notes_off_is_written_and_unknown_events_fail(tmp_path):
    path = tmp_path / "off.mid"
    write_midi(path, [MidiEvent(2., "all_notes_off", 9)], 100, duration_s=3.)
    messages = list(mido.MidiFile(path))
    assert any(m.type == "control_change" and m.control == 123 and m.channel == 9 for m in messages)
    assert sum(m.time for m in messages) == pytest.approx(3.)
    with pytest.raises(ValueError, match="unsupported"):
        write_midi(path, [MidiEvent(0, "bogus")], 100)


@pytest.mark.parametrize("time", [-.1, float("inf"), float("nan")])
def test_invalid_timestamp_is_rejected(tmp_path, time):
    with pytest.raises(ValueError):
        write_midi(tmp_path / "bad.mid", [MidiEvent(time, "note_on", 0, 60, 80)], 100)


def test_projection_removes_session_offset_but_retains_phrase_leading_rest():
    performance = PlannedPerformance((Note(60, 20.7, .53, 91, "lead"),), (), None, 120, 5, 1)
    events = backend.midi_events(performance)
    assert events[0].timestamp == pytest.approx(.35)
    assert events[1].timestamp == pytest.approx(.615)
    assert events[0].channel == 4 and events[0].velocity == 91


def test_success_requires_verified_files_before_state_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "render_with_fluidsynth", fake_render)
    manager, plan = staged()
    before = manager.state
    r = runtime(tmp_path)
    result = r.execute(manager, plan)
    assert manager.state.state_version == before.state_version + 1
    assert manager.state.layer("drums").level > before.layer("drums").level
    assert manager.state.tempo_bpm == 103.7
    assert result.audio.shape[1] == 2
    assert (r.last_artifacts / "receipt.json").is_file()
    with pytest.raises(PlanError):
        r.execute(manager, plan)


def test_failed_synth_does_not_apply_state(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("test synth failure")
    monkeypatch.setattr(backend, "render_with_fluidsynth", fail)
    manager, plan = staged()
    before = manager.state
    r = runtime(tmp_path)
    with pytest.raises(RuntimeError, match="test synth failure"):
        r.execute(manager, plan)
    assert manager.state == before
    assert manager.plan_status(plan.plan_id) == "failed"
    assert (r.last_artifacts / "band.mid").is_file()


def test_cancelled_inflight_export_never_applies_obsolete_state(tmp_path, monkeypatch):
    manager, plan = staged()
    def new_input(*args):
        fake_render(*args)
        manager.ingest([from_text("停止", session_id="kevin_test", event_id="e2")])
    monkeypatch.setattr(backend, "render_with_fluidsynth", new_input)
    r = runtime(tmp_path)
    with pytest.raises(PlanError):
        r.execute(manager, plan)
    assert manager.plan_status(plan.plan_id) == "cancelled"
    assert manager.state.layer("drums").level == .5


def test_corrupted_midi_is_detected(tmp_path):
    path = tmp_path / "wrong.mid"
    events = [MidiEvent(.5, "note_on", 4, 60, 80), MidiEvent(1., "note_off", 4, 60, 0)]
    write_midi(path, events, 100, {4: 73}, duration_s=2.)
    events[0] = MidiEvent(.5, "note_on", 4, 61, 80)
    with pytest.raises(ValueError, match="pitch"):
        backend.verify_midi(path, events, {4: 73}, 2.)


def test_quantized_adjacent_notes_release_before_retrigger(tmp_path):
    events = [MidiEvent(0, "note_on", 4, 60, 80), MidiEvent(1.000001, "note_off", 4, 60, 0),
              MidiEvent(1., "note_on", 4, 60, 90), MidiEvent(2., "note_off", 4, 60, 0)]
    path = tmp_path / "retrigger.mid"
    write_midi(path, events, 120, {4: 73}, duration_s=2.)
    notes = [m for m in mido.MidiFile(path).tracks[0] if m.type in {"note_on", "note_off"}]
    assert [m.type for m in notes] == ["note_on", "note_off", "note_on", "note_off"]
    assert notes[2].time == 0
    backend.verify_midi(path, events, {4: 73}, 2.)


def test_same_pitch_overlap_rejected_different_pitch_overlap_supported(tmp_path, monkeypatch):
    manager, plan = staged()
    performance = PlannedPerformance((Note(60, 0, 1, 80, "lead"), Note(60, .5, 1, 80, "lead")), (), None, 100, 0, 1)
    monkeypatch.setattr(backend, "prepare_plan", lambda *a, **k: performance)
    r = runtime(tmp_path)
    with pytest.raises(PlanError, match="same-pitch"):
        r.validate(manager.state, plan)
    performance = PlannedPerformance((Note(60, 0, 1, 80, "lead"), Note(64, .5, 1, 80, "lead")), (), None, 100, 0, 1)
    assert r.validate(manager.state, plan) == performance
