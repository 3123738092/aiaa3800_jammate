import base64
import copy
import io
import json
from pathlib import Path

import mido
import numpy as np
import pytest
import soundfile as sf

from jammate.analysis.rhythm import _onsets_and_accents
from jammate.contracts import InputEvent, InputEventError
from jammate.perception import rhythm_event, rhythm_midi, text_event


SAMPLES = Path(__file__).resolve().parents[1] / "examples" / "input-event-handoff"


def sample(name):
    return json.loads((SAMPLES / f"{name}.input-event.json").read_bytes())


def test_three_files_survive_public_contract():
    for name in ("text", "humming", "rhythm"):
        event = sample(name)
        assert InputEvent.from_dict(event).to_dict() == event


def test_text_producer_preserves_exact_input_and_caller_identity():
    original = sample("text")
    body = {"session_id": original["session_id"], "event_id": original["event_id"],
            "text": (SAMPLES / "text.txt").read_bytes().decode("utf-8")}
    assert text_event(body) == original


def test_free_rhythm_reuses_upstream_results_on_the_original_clock():
    wav = (SAMPLES / "rhythm.wav").read_bytes()
    signal, sr = sf.read(io.BytesIO(wav), dtype="float32")
    times, accents = _onsets_and_accents(signal, sr)
    event = rhythm_event({"session_id": "test", "started_at": 2, "wav": base64.b64encode(wav)})
    assert event["payload"] == sample("rhythm")["payload"]
    assert event["payload"]["rhythm"] == {"onset_seconds": list(times), "accents": list(accents)}
    assert event["ended_at"] == pytest.approx(2 + len(signal) / sr)


def test_edit_uses_current_accents_for_midi():
    event = sample("rhythm")
    event["payload"]["rhythm"]["accents"][0] = 45 / 127
    assert event["payload"]["raw_rhythm"] == sample("rhythm")["payload"]["rhythm"]
    result = rhythm_midi({"event": event})
    midi = mido.MidiFile(file=io.BytesIO(base64.b64decode(result["midi"])))
    notes = [message for message in midi if message.type == "note_on"]
    assert len(notes) == len(event["payload"]["rhythm"]["onset_seconds"])
    assert result["hits"][0]["velocity"] == notes[0].velocity == 45


def test_silence_and_invalid_revisions():
    wave = io.BytesIO()
    sf.write(wave, np.zeros(16000), 16000, format="WAV")
    empty = rhythm_event({"session_id": "quiet", "wav": base64.b64encode(wave.getvalue())})
    assert empty["payload"]["status"] == "no_onsets"
    assert empty["payload"]["rhythm"] == {"onset_seconds": [], "accents": []}
    for change in ("outside_duration", "missing_accent"):
        invalid = copy.deepcopy(sample("rhythm"))
        rhythm = invalid["payload"]["rhythm"]
        if change == "outside_duration":
            rhythm["onset_seconds"][-1] = invalid["payload"]["audio_duration_s"]
        else:
            rhythm["accents"].pop()
        with pytest.raises(InputEventError):
            InputEvent.from_dict(invalid)
