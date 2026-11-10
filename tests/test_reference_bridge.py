import base64
import io
import os
from pathlib import Path
import subprocess
import sys

import pretty_midi
import pytest
import soundfile

from jammate.reference_bridge import REFERENCE_COMMIT, ReferenceBridge


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    with ReferenceBridge(ROOT, scratch=tmp_path_factory.mktemp("reference")) as connection:
        yield connection


def midi(result):
    return pretty_midi.PrettyMIDI(io.BytesIO(base64.b64decode(result["midi"])))


def test_original_rhythm_analysis_and_generation_use_the_pinned_checkout(bridge):
    wav = base64.b64encode((ROOT / "web/rhythm-example.wav").read_bytes()).decode("ascii")
    event = bridge.request("rhythm/analyze", {"wav": wav, "session_id": "test-rhythm",
                           "count_in": 4, "tempo": 100, "origin": None, "started_at": 0})
    rhythm = event["payload"]["rhythm"]
    assert rhythm["onset_beats"] == [0, 1, 1.5, 3]
    expected = {
        "follow": {0, 1, 1.5, 3},
        "vary": {0, .25, .5, 1, 1.5, 3},
    }
    for mode, beats in expected.items():
        result = bridge.request("rhythm/create", {"event": event, "mode": mode, "energy": 2, "bars": 2})
        drums = next(part for part in midi(result).instruments if part.is_drum)
        first_bar = {round(note.start * rhythm["tempo_bpm"] / 60, 2) for note in drums.notes
                     if note.start * rhythm["tempo_bpm"] / 60 < 3.99}
        assert first_bar == beats
        assert result["reference_commit"] == REFERENCE_COMMIT
        wave, sr = soundfile.read(io.BytesIO(base64.b64decode(result["wav"])))
        assert len(wave) / sr > 4 and abs(wave).max() > .001


def test_original_text_turns_keep_state_and_do_not_reapply_successful_requests(bridge):
    base = {"session_id": "test-text", "agent": "rule", "bpm": 100, "profile": "electric"}
    first = bridge.request("text/turn", {**base, "after_turn": 0, "request_id": "first", "text": "来一段，慢一点"})
    assert first["start_bar"] == 0
    assert first["tempo_bpm"] == 92
    assert any(part.name == "bass" for part in midi(first).instruments)

    command = {**base, "after_turn": 1, "request_id": "next", "text": "去掉贝斯"}
    second = bridge.request("text/turn", command)
    assert second["start_bar"] == first["bars"]
    assert second["tempo_bpm"] == 92
    assert all(part.name != "bass" for part in midi(second).instruments)
    assert bridge.request("text/turn", command) == second
    with pytest.raises(ValueError, match="更新的段落"):
        bridge.request("text/turn", {**command, "request_id": "stale-new", "text": "加上贝斯"})


def test_pinned_llm_uses_the_original_context_validation_and_renderer(bridge):
    # Run in a fresh interpreter so the upstream jammate package owns the
    # imports. Substitute only the external provider, never the Agent.
    snapshot = bridge._snapshot()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (snapshot / "src", snapshot, ROOT))))
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tests/reference_llm_probe.py")],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
