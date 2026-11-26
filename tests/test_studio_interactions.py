
from contextlib import closing
from unittest.mock import Mock
import wave

import pytest
from jammate.browser_jam import BrowserJamService
from jammate import opening_mic

def apply_next(service, session_id, packet):
    view = service.dispatch("poll", {"session": session_id, "started": packet["bar"],
                                     "sample": packet["start"], "buffered": packet["bar"]})
    session = service.sessions[session_id]
    if session.future:
        session.future.result(timeout=4)
    return service.dispatch("poll", {"session": session_id, "started": packet["bar"],
                                      "sample": packet["end"] - 1000, "buffered": packet["bar"]})

@pytest.mark.parametrize("phrase,meter,beats", [
    ("从 4/4 换到 3/4，鼓轻一点", "3/4", 3),
    ("切换成八分之六拍", "6/8", 2),
])
def test_studio_switches_live_meter_on_confirmed_audio_boundary(phrase, meter, beats):
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening_mode": "cold", "opening": {"text": "完整乐队伴奏，96 BPM"},
            "bpm": 96, "meter": "4/4",
        })
        session_id = view["session"]
        view = apply_next(service, session_id, view["next"])
        service.dispatch("studio-input", {"session": session_id, "input": {
            "text": phrase, "source": "speech", "target": "band",
        }})
        before = view["next"]
        assert before["end"]-before["start"] == pytest.approx(view["sample_rate"] * 4 * 60/96, abs=1)
        for _ in range(7):
            packet = view["next"]
            view = apply_next(service, session_id, packet)
            if view["feedback"]["status"] == "applied":
                break
        assert view["session"] == session_id and view["meter"] == meter
        assert view["feedback"]["playback"]["bar"] == packet["bar"]
        assert packet["end"]-packet["start"] == pytest.approx(view["sample_rate"] * beats * 60/96, abs=1)
        assert view["next"]["start"] == packet["end"]
        frame = service.dispatch("debug", {"session": session_id})
        if "鼓轻一点" in phrase:
            assert frame["jam_state"]["arrangement"]["layers"][0]["level"] < 1.0

@pytest.mark.parametrize("phrase,meter,beats", [
    ("给我一个3拍子的鼓点", "3/4", 3),
    ("来一个八分之六拍的鼓点", "6/8", 2),
])
def test_opening_speech_meter_overrides_default_dial(phrase,meter,beats):
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening_mode": "cold", "bpm": 96, "meter": "4/4",
            "opening": {"text": phrase, "source": "speech", "target": "drums"},
        })
        actual = view["next"]["end"] - view["next"]["start"]
        assert view["meter"] == meter
        assert actual == pytest.approx(view["sample_rate"] * beats * 60/96, abs=1)

def test_asr_normalizes_only_known_music_terms_and_preserves_the_raw_transcript(monkeypatch):
    from test_opening_mic import recording

    asr = Mock()
    asr.transcribe.side_effect = [
        "嘿，旋律组，加一段boss and Nova的旋律。", "boss and Nova 在会议室",
        "我老板在说话", "嘿，旋律组，保留四分之三拍。",
    ]
    monkeypatch.setattr(opening_mic.OpeningMic, "_create_transcriber", Mock(return_value=asr))
    body = {"mode": "wake", "audio": recording()}
    with closing(opening_mic.OpeningMic()) as mic:
        result = mic.recognize(body)
        assert result["target"] == "keys"
        assert result["instruction"] == "加一段Bossa Nova的旋律。"
        assert "boss and Nova" in result["raw_transcript"]
        assert mic.recognize(body) == mic.recognize(body) == {}
        assert mic.recognize(body) == {"target":"keys","instruction":"保留四分之三拍。"}

def test_playing_drum_only_jam_accepts_bossa_melody_without_requesting_a_key():
    with closing(BrowserJamService()) as service:
        opening = service.dispatch("start", {
            "opening_mode": "cold", "opening": {"text": "来个鼓点"}, "meter": "4/4", "bpm": 96,
        })
        session = opening["session"]
        view = apply_next(service, session, opening["next"])
        frame = service.dispatch("debug", {"session": session})
        assert frame["key"] is None
        drum_pattern = [(n["pitch"], n["start"], n["duration"], n["velocity"])
                        for n in frame["current"]["notes"] if n["instrument"] == "drums"]
        service.dispatch("studio-input", {"session": session, "input": {
            "text": "你在现在这个鼓的基础上，加一段 Bossa Nova 的旋律。", "source": "speech", "target": "band",
        }})
        for _ in range(7):
            view = apply_next(service, session, view["next"])
            if view["feedback"]["status"] == "applied":
                break
        assert view["feedback"]["status"] == "applied"
        frame = service.dispatch("debug", {"session": session})
        notes = frame["current"]["notes"]
        assert any(n["instrument"] == "lead" for n in notes)
        assert [(n["pitch"], n["start"], n["duration"], n["velocity"])
                for n in notes if n["instrument"] == "drums"] == drum_pattern
        assert frame["key"] == "C major"


def test_mrt2_preview_can_use_confirmed_score_after_optional_arranger_rejects(monkeypatch):
    from jammate.contracts import PlanError

    class RejectedArranger:
        def __init__(self, *args, **kwargs):
            self.last_trace = {}

        def mrt(self, *args, **kwargs):
            self.last_trace = {"status": "failed", "failure": "invalid generated accents"}
            raise PlanError("invalid generated accents")

    def generate_preview(style, *, output, duration, midi, **kwargs):
        assert midi.is_file()
        assert style == "bossa nova"
        with wave.open(str(output), "wb") as audio:
            audio.setparams((2, 2, 48000, 0, "NONE", ""))
            audio.writeframes(b"\x80\x02\x80\x02" * round(duration * 48000))
        return {"inference": "test_preview"}

    monkeypatch.setattr(BrowserJamService, "mrt2_available", staticmethod(lambda: True))
    monkeypatch.setattr("jammate.browser_jam.BrowserJam.start_opening_preview", lambda self: None)
    monkeypatch.setattr("jammate.browser_jam.LLMBackendArranger", RejectedArranger)
    monkeypatch.setattr("jammate.browser_jam.generate_isolated", generate_preview)
    monkeypatch.setattr(BrowserJamService, "config", staticmethod(lambda: Mock(timeout_s=.1)))
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {
            "opening_mode": "cold", "opening": {"text": "来个鼓点"},
            "renderer": "mrt2", "allow_preview": True, "arranger": "llm_all",
        })
        session_id = view["session"]
        view = apply_next(service, session_id, view["next"])
        result = service.dispatch("studio-input", {"session": session_id, "input": {
            "text": "在鼓的基础上，加一段 Bossa Nova 的旋律。", "source": "speech", "target": "band",
        }})
        for _ in range(7):
            view = apply_next(service, session_id, view["next"])
            if view["feedback"]["status"] == "applied":
                break
        assert view["feedback"]["status"] == "applied"
        session = service.sessions[session_id]
        session.preview_future.result(timeout=4)
        effective = view["current_bar"]
        view = service.dispatch("poll", {"session": session_id, "started": effective,
                                        "sample": session.playing.timing.start_sample, "buffered": effective})
        assert view["preview_status"]["status"] == "completed"
        preview = service.dispatch("preview", {"session": session_id, "id": result["feedback"]["id"]})
        assert preview["wav"]
        turn = service.dispatch("debug", {"session": session_id})["turns"][-1]
        assert turn["arrangement"]["status"] == "fallback"
        assert turn["playback_receipt"]["bar"] == effective


def test_qwen_primary_falls_back_to_iflytek_for_a_wake_command(monkeypatch):
    from test_opening_mic import recording

    qwen = Mock()
    qwen.transcribe.side_effect = opening_mic.QwenRecognitionError("temporary outage")
    xfyun = Mock()
    xfyun.transcribe.return_value = "嘿，节奏组。"
    monkeypatch.setenv("JAMMATE_ASR_PRIMARY", "qwen")
    monkeypatch.setattr(opening_mic.OpeningMic, "_create_fallback_transcriber", Mock(return_value=qwen))
    monkeypatch.setattr(opening_mic.OpeningMic, "_create_transcriber", Mock(return_value=xfyun))
    with closing(opening_mic.OpeningMic()) as mic:
        result = mic.recognize({"mode": "wake", "audio": recording()})
        assert result == {"target": "drums", "instruction": ""}
    qwen.transcribe.assert_called_once()
    xfyun.transcribe.assert_called_once()
