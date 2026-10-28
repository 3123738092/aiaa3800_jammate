import json
from types import SimpleNamespace

import numpy as np
import pytest

from jammate.director.policy import UserRhythm
from jammate.live import BLOCK, PRE_ROLL_SAMPLES, LiveJam
from jammate.render.synth import SR
from jammate.rhythm import RhythmContext
from jammate.session_log import SessionLog
from jammate.state import JamState

pytestmark = pytest.mark.unit

A = RhythmContext(100, (0, 1, 1.5, 3), (1, .5, .8, 1))
B = RhythmContext(120, (0, .5, 2, 3.5), (1, .5, .8, 1))


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_live_update_changes_next_unrendered_bar_and_preserves_previous_audio(tmp_path):
    path = tmp_path / "live.jsonl"
    jam = LiveJam(JamState(tempo_bpm=100, rhythm=A), 0, SessionLog(path), input_mode="rhythm")
    jam._render_next_bar()
    old_end = jam._next_bar_sample
    previous_audio = jam._timeline.read(old_end).copy()
    jam.apply(UserRhythm(B))
    jam._render_next_bar()
    bars = [r for r in _records(path) if r["type"] == "bar_scheduled"]
    assert bars[0]["tempo_bpm"] == 100
    assert bars[1]["tempo_bpm"] == 108  # bounded change at the bar boundary
    assert bars[1]["start_sample"] == old_end
    assert bars[1]["rhythm"]["onset_beats"] == list(B.onset_beats)
    assert previous_audio.any()
    decision = next(r for r in _records(path) if r["type"] == "decision")
    assert decision["effective_sample"] == old_end and decision["effective_bar"] == 1


def test_duplex_callback_maps_adc_to_dac_clock_and_worker_logs_device_playback(tmp_path):
    path = tmp_path / "live.jsonl"
    jam = LiveJam(JamState(rhythm=A), 0, SessionLog(path), input_mode="rhythm")
    jam._render_next_bar()
    jam._timeline.read(PRE_ROLL_SAMPLES)
    timing = SimpleNamespace(inputBufferAdcTime=10, outputBufferDacTime=10.1)
    out = np.zeros((BLOCK, 1), dtype=np.float32)
    jam._duplex_callback(np.ones_like(out), out, BLOCK, timing, False)
    sample, audio = jam._mic.get_nowait()
    assert sample == PRE_ROLL_SAMPLES - round(.1 * SR)
    assert np.array_equal(audio, np.ones(BLOCK))
    # The callback itself performs no file logging.
    assert not any(r["type"] == "bar_playback" for r in _records(path))
    jam._log_playback()
    playback = next(r for r in _records(path) if r["type"] == "bar_playback")
    assert playback["start_sample"] == PRE_ROLL_SAMPLES
    assert playback["device_dac_time_s"] == pytest.approx(10.1)


def test_live_overrun_uses_whole_bar_skip_instead_of_restarting_midbeat(tmp_path):
    path = tmp_path / "live.jsonl"
    jam = LiveJam(JamState(tempo_bpm=120, rhythm=B), 0, SessionLog(path), input_mode="rhythm")
    jam._render_next_bar()
    jam._timeline.read(jam._next_bar_sample + BLOCK)
    jam._render_next_bar()
    bars = [r for r in _records(path) if r["type"] == "bar_scheduled"]
    assert bars[1]["bar"] == 2
    assert bars[1]["start_sample"] == PRE_ROLL_SAMPLES + 4 * SR


def test_rhythm_input_does_not_autonomously_add_a_lead():
    jam = LiveJam(JamState(rhythm=A), 0, SessionLog(None), input_mode="rhythm")
    for _ in range(4):
        jam._render_next_bar()
    assert not jam._leads and not jam._state.last_ai_motif


def test_microphone_bar_flows_through_analysis_and_director_without_pitch_tracking(monkeypatch):
    jam = LiveJam(JamState(tempo_bpm=100), 0, SessionLog(None), input_mode="rhythm")
    jam._render_next_bar()
    timing = jam._bar_timings[0]
    audio = np.zeros(timing.end_sample - timing.start_sample, dtype=np.float32)
    rng = np.random.default_rng(0)
    for beat in (0, 1, 1.5, 3):
        start, size = round(beat * .6 * SR), round(.03 * SR)
        audio[start:start + size] = rng.standard_normal(size) * .2 * np.exp(-np.linspace(0, 6, size))
    original_apply = jam.apply

    def apply_and_finish(event):
        original_apply(event)
        jam._stop.set()

    monkeypatch.setattr(jam, "apply", apply_and_finish)
    jam._mic.put((timing.start_sample, audio))
    jam._rhythm_listener()
    assert jam._state.rhythm.onset_beats == (0, 1, 1.5, 3)
    assert jam._state.tempo_bpm == 100
