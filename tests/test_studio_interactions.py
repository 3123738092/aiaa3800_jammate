
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


def test_studio_can_compare_speech_providers_without_fallback(monkeypatch):
    from test_opening_mic import recording

    xfyun, qwen = Mock(), Mock()
    xfyun.transcribe.return_value = "嗨节奏组，加一点切分。"
    qwen.transcribe.side_effect = ["嗨旋律组，试一下 Bossa Nova。", opening_mic.QwenRecognitionError("unavailable")]
    monkeypatch.setattr(opening_mic.OpeningMic, "_create_transcriber", Mock(return_value=xfyun))
    monkeypatch.setattr(opening_mic.OpeningMic, "_create_fallback_transcriber", Mock(return_value=qwen))
    body = {"mode": "wake", "audio": recording()}
    with closing(opening_mic.OpeningMic()) as mic:
        assert mic.recognize(dict(body, asr_provider="xfyun"))["target"] == "drums"
        result = mic.recognize(dict(body, asr_provider="qwen"))
        assert result["target"] == "keys"
        assert result["asr_provider"] == "qwen"
        with pytest.raises(ValueError, match="unavailable"):
            mic.recognize(dict(body, asr_provider="qwen"))
    assert xfyun.transcribe.call_count == 1


def test_mrt2_direct_can_open_with_style_only_and_an_explicit_meter(monkeypatch):
    monkeypatch.setattr(BrowserJamService, "mrt2_stream_available", staticmethod(lambda: True))
    with closing(BrowserJamService()) as service:
        view = service.dispatch("start", {"renderer": "mrt2_direct", "opening_mode": "cold",
            "meter": "4/4", "opening": {"text": "bossa nova trio, nylon guitar, light percussion"}})
        details = service.dispatch("debug", {"session": view["session"]})
        assert view["next"]["pcm_frames"] > 0
        assert details["opening"]["instruments"] == []
        assert "bossa nova" in details["jam_state"]["arrangement"]["style"].casefold()


def test_stream_prefetch_does_not_confirm_playback(monkeypatch):
    monkeypatch.setattr(BrowserJamService, "mrt2_stream_available", staticmethod(lambda: True))
    with closing(BrowserJamService()) as service:
        opening = service.dispatch("start", {"renderer": "mrt2_stream", "stream_lookahead": True,
            "opening_mode": "cold", "opening": {"text": "Full band accompaniment, 96 BPM"}})
        session = opening["session"]
        first = opening["next"]
        assert first["bar"] == 0 and first["following"]["bar"] == 1
        assert opening["current_bar"] is None
        assert service.dispatch("poll", {"session": session, "started": -1, "sample": 0,
                                        "buffered": 1})["current_bar"] is None
        started = service.dispatch("poll", {"session": session, "started": 0, "sample": first["start"],
                                            "buffered": 1})
        assert started["current_bar"] == 0 and started["next"]["bar"] == 2
        followed = service.dispatch("poll", {"session": session, "started": 1, "sample": first["end"],
                                             "buffered": 2})
        assert followed["current_bar"] == 1 and followed["next"]["bar"] == 3


def test_warm_opening_can_repair_invalid_provider_selection():
    from types import SimpleNamespace
    import json

    from jammate.contracts.adapters import from_text
    from jammate.orchestration.jam_opening import prepare_opening
    from jammate.orchestration.llm_config import LLMConfig

    valid = {"tempo_bpm": 96, "tonic": None, "mode": None, "chords": [],
             "rhythm_steps": [0, 4, 8, 12], "instruments": ["drums"], "reason": "Jazz drums"}
    provider = Mock()
    provider.complete.side_effect = [
        SimpleNamespace(text=json.dumps(dict(valid, rhythm_steps=[0, 0])), model="test", usage={}),
        SimpleNamespace(text=json.dumps(valid), model="test", usage={}),
    ]
    event = from_text("适合在深夜练习的爵士节奏", session_id="warm-check")
    state, opening = prepare_opening((event,), LLMConfig("test-key"), {}, provider=provider)
    assert state.layer("drums").active and opening["model"] == "test"
    first, second = provider.complete.call_args_list
    assert "duplicate rhythm steps" in second.args[0][-1]["content"]
    assert 0 < second.args[2] <= first.args[2]


def test_mrt2_score_additions_replace_solo_condition_and_keep_drums():
    from jammate.contracts.adapters import from_text
    from jammate.generate.arranger import arrange_bar
    from jammate.interaction import Layer
    from jammate.orchestration.plans import make_plan
    from jammate.orchestration.transitions import project
    from jammate.state import JamState
    from mrt2_demo.plan_conditioning import score_stream_style

    state = JamState(key=None, auto_harmony=False, style="solo drum groove, no melody or harmony",
                     layers=(Layer("drums"), Layer("bass", active=False),
                             Layer("keys", active=False), Layer("lead", active=False)))
    event = from_text("加上贝斯和干净的电吉他", session_id=state.session_id)
    actions = [
        {"action": "SET_KEY", "scope": "band", "strength": .5, "parameters": {"tonic": 4, "mode": "major"}},
        {"action": "ADD_LAYER", "scope": "bass", "strength": .5, "parameters": {}},
        {"action": "INTRODUCE_LAYER", "scope": "gm_27", "strength": .5, "parameters": {}},
    ]
    plan = make_plan(state, (event,), "layers", "FOLLOW", actions, ["layer:drums", "groove"], {}, 1,
                     "Add bass and guitar while keeping the drum groove.", [])
    target = project(state, plan)
    notes = arrange_bar(target, 0)
    assert {note.instrument for note in notes} >= {"drums", "bass", "gm_27"}
    assert tuple(note for note in notes if note.instrument == "drums") == arrange_bar(state, 0)
    prompt = score_stream_style(target.style, target)
    assert "bass" in prompt and "electric guitar (clean)" in prompt
    assert "solo" not in prompt and "no melody or harmony" not in prompt


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
