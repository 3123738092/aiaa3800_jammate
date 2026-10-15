import json

import pretty_midi
import pytest
import soundfile as sf

from jammate.cli import main

pytestmark = pytest.mark.integration


def test_demo_input_then_offline_jam(tmp_path):
    hum = tmp_path / "hum.wav"
    assert main(["demo-input", "--out", str(hum)]) == 0
    audio, sr = sf.read(hum)
    assert sr == 22050 and audio.ndim == 1

    jam, midi, log = tmp_path / "jam.wav", tmp_path / "jam.mid", tmp_path / "session.jsonl"
    code = main(["offline", str(hum), "--out", str(jam), "--midi", str(midi),
                 "--log", str(log), "--bars", "6", "--seed", "1"])
    assert code == 0
    assert sf.info(jam).duration >= sf.info(hum).duration
    assert pretty_midi.PrettyMIDI(str(midi)).instruments
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert records[0]["type"] == "analysis"
    assert any(r["type"] == "decision" and r["action"] == "follow" for r in records)


def test_missing_input_file_is_a_clear_error(tmp_path, caplog):
    assert main(["offline", str(tmp_path / "nope.wav")]) == 2
    assert "not found" in caplog.text


@pytest.mark.parametrize("value", ["-50", "0", "abc"])
def test_bad_tempo_is_rejected_by_the_parser(value, tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["offline", str(tmp_path / "x.wav"), "--tempo", value])
    assert exc.value.code == 2


def test_unreadable_audio_is_a_clear_error(tmp_path, caplog):
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not really a wav file")
    assert main(["offline", str(broken), "--out", str(tmp_path / "jam.wav")]) == 2
    assert "could not read" in caplog.text


def test_unwritable_output_is_a_clear_error(tmp_path, caplog):
    hum = tmp_path / "hum.wav"
    main(["demo-input", "--out", str(hum)])
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so it cannot be a directory")
    assert main(["offline", str(hum), "--out", str(blocker / "jam.wav"), "--bars", "2"]) == 2
    assert "could not write" in caplog.text
