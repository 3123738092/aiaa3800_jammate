import json

import numpy as np
import pretty_midi
import pytest
import soundfile as sf

from jammate.cli import main

pytestmark = pytest.mark.integration


def test_json_cli_exports_all_modes_and_roundtrips_the_rhythm(tmp_path):
    path = tmp_path / "input.json"
    data = dict(tempo_bpm=100, onset_beats=[0, 1, 1.5, 3], accents=[1, .5, .8, 1])
    path.write_text(json.dumps(data))
    counts = {}
    for mode in ("follow", "vary", "simplify"):
        wav, midi = tmp_path / f"{mode}.wav", tmp_path / f"{mode}.mid"
        log, context = tmp_path / f"{mode}.jsonl", tmp_path / f"{mode}.json"
        assert main(["rhythm", str(path), "--out", str(wav), "--midi", str(midi),
                     "--mode", mode, "--bars", "2", "--log", str(log),
                     "--rhythm-out", str(context)]) == 0
        tracks = pretty_midi.PrettyMIDI(str(midi)).instruments
        assert {t.name for t in tracks} == {"drums", "bass", "keys"}
        counts[mode] = len(next(t for t in tracks if t.name == "bass").notes)
        assert sf.info(wav).duration == pytest.approx(6.3, abs=.001)
        assert json.loads(context.read_text())["onset_beats"] == data["onset_beats"]
        assert json.loads(log.read_text())["mode"] == mode
    assert counts["simplify"] < counts["follow"] < counts["vary"]


@pytest.mark.parametrize("data", ["not json", "[]", '{"tempo_bpm": "fast"}',
                                  '{"tempo_bpm":100,"onset_beats":[],"accents":[]}'])
def test_bad_rhythm_input_is_a_clear_cli_error(tmp_path, caplog, data):
    path = tmp_path / "bad.json"
    path.write_text(data)
    out = tmp_path / "jam.wav"
    assert main(["rhythm", str(path), "--out", str(out)]) == 2
    assert not out.exists()
    assert "could not generate" in caplog.text


def test_clap_recording_exports_wav_midi_extracted_json_and_log(tmp_path):
    sr = 22050
    audio = np.zeros(round(5.15 * sr), dtype=np.float32)
    rng = np.random.default_rng(42)
    for time_s in (.3, .9, 1.5, 2.1, 2.7, 3.3, 3.6, 4.5):
        start, size = round(time_s * sr), round(.03 * sr)
        audio[start:start + size] = .2 * rng.standard_normal(size) * np.exp(-np.linspace(0, 6, size))
    source, wav, midi = tmp_path / "claps.wav", tmp_path / "jam.wav", tmp_path / "jam.mid"
    extracted, log = tmp_path / "rhythm.json", tmp_path / "log.jsonl"
    sf.write(source, audio, sr)
    assert main(["rhythm-audio", str(source), "--out", str(wav), "--midi", str(midi),
                 "--rhythm-out", str(extracted), "--log", str(log), "--bars", "2"]) == 0
    context = json.loads(extracted.read_text())
    assert context["onset_beats"] == [0, 1, 1.5, 3]
    assert context["tempo_bpm"] == pytest.approx(100, abs=2)
    assert sf.info(wav).duration > 4.5
    tracks = pretty_midi.PrettyMIDI(str(midi)).instruments
    assert {t.name for t in tracks} == {"drums", "bass", "keys"}
    assert json.loads(log.read_text())["context"] == context
