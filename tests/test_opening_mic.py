import base64
import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from jammate import opening_mic


def recording(sample=b"\x10\x01"):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", ""))
        writer.writeframes(sample * 1600)
    return base64.b64encode(stream.getvalue()).decode()


def test_service_prepares_one_client_for_intent_and_dictation(monkeypatch):
    config = SimpleNamespace(api="chat_completions", timeout_s=10)
    load_config = Mock(return_value=config)
    factory = Mock()
    provider = factory.return_value
    provider.config = config
    provider.complete.side_effect = [
        opening_mic.ProviderFailure("timeout"),
        SimpleNamespace(text='{"route":"WAIT_TEXT","instruction":""}'),
        SimpleNamespace(text='{"route":"SEND_TEXT","instruction":"慢一点"}'),
    ]
    monkeypatch.setattr(opening_mic.LLMConfig, "load", load_config)
    monkeypatch.setattr(opening_mic, "OpenAIProvider", factory)
    body = {"audio": recording(), "mode": "intent"}
    with closing(opening_mic.OpeningMic()) as mic:
        factory.assert_called_once_with(config, schema_name="jammate_opening_mic")
        provider.complete.assert_not_called()
        with pytest.raises(ValueError, match="timeout"):
            mic.recognize(body)
        assert mic.recognize(body)["route"] == "WAIT_TEXT"
        assert mic.recognize({**body, "mode": "dictation"})["instruction"] == "慢一点"
        load_config.assert_called_once()
        provider.client.close.assert_not_called()
    provider.client.close.assert_called_once_with()


def test_overlapping_recordings_keep_the_client_open_until_both_finish(monkeypatch):
    entered = Barrier(3)
    finish = Event()
    closing_started = Event()
    provider = Mock()
    provider.config.timeout_s = 10

    def complete(*args):
        entered.wait(timeout=5)
        assert finish.wait(timeout=5)
        return SimpleNamespace(text='{"route":"WAIT_HUMMING","instruction":""}')

    provider.complete.side_effect = complete
    factory = Mock(return_value=provider)
    monkeypatch.setattr(opening_mic.OpeningMic, "_create_provider", factory)
    mic = opening_mic.OpeningMic()

    def close():
        closing_started.set()
        mic.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        requests = [pool.submit(mic.recognize, {"audio": recording(), "mode": "intent"}) for _ in range(2)]
        entered.wait(timeout=5)
        shutdown = pool.submit(close)
        try:
            assert closing_started.wait(timeout=5)
            provider.client.close.assert_not_called()
        finally:
            finish.set()
        assert all(request.result(timeout=5)["route"] == "WAIT_HUMMING" for request in requests)
        shutdown.result(timeout=5)
    factory.assert_called_once()
    provider.client.close.assert_called_once_with()


def test_live_dictation_resamples_browser_audio_for_the_shared_provider(monkeypatch):
    provider = Mock()
    provider.config.timeout_s = 10
    provider.complete.return_value = SimpleNamespace(text='{"route":"SEND_TEXT","instruction":"鼓少一点"}')
    monkeypatch.setattr(opening_mic.OpeningMic, "_create_provider", Mock(return_value=provider))
    with closing(opening_mic.OpeningMic()) as mic:
        audio = .3 * np.sin(2 * np.pi * 330 * np.arange(24000) / 48000)
        assert mic.ready
        assert mic.dictate(audio, 48000) == {"route": "SEND_TEXT", "instruction": "鼓少一点"}
        messages, schema, _ = provider.complete.call_args.args
        assert messages[0]["content"] == opening_mic.DICTATE
        part, = messages[1]["content"]
        with wave.open(io.BytesIO(base64.b64decode(part["input_audio"]["data"]))) as clip:
            assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth(), clip.getnframes()) == (16000, 1, 2, 8000)
        assert schema["properties"]["route"]["enum"] == ["SEND_TEXT", "KEEP_LISTENING"]


@pytest.mark.parametrize("mode,route,words", [
    ("intent", "WAIT_HUMMING", ""), ("intent", "WAIT_RHYTHM", ""),
    ("intent", "WAIT_TEXT", ""), ("intent", "SEND_TEXT", "慢一点，先别加鼓。"),
    ("dictation", "SEND_TEXT", "先用钢琴，速度九十。"),
])
def test_cloud_routes_audio_and_preserves_words(monkeypatch, mode, route, words):
    sent = {}
    class Provider:
        def __init__(self, config, *, schema_name):
            self.config = config
            self.client = Mock()

        def complete(self, messages, schema, timeout_s):
            sent["audio"] = messages[1]["content"][0]["input_audio"]
            return SimpleNamespace(text=json.dumps({"route": route, "instruction": words}))

    monkeypatch.setattr(opening_mic, "OpenAIProvider", Provider)
    monkeypatch.setattr(opening_mic.LLMConfig, "load", lambda *a, **k: SimpleNamespace(api="chat_completions", timeout_s=10))
    audio = recording()
    with closing(opening_mic.OpeningMic()) as mic:
        result = mic.recognize({"audio": audio, "mode": mode})
    assert result["route"] == route and result.get("instruction", "") == words
    assert sent["audio"] == {"format": "wav", "data": audio}


@pytest.mark.parametrize("words,expected", [
    ("我想拍几个拍子给你听。", "WAIT_RHYTHM"),
    ("我来哼一段旋律给你听。", "WAIT_HUMMING"),
    ("我想跟你说几句话。", "WAIT_TEXT"),
    ("你给我拍几个拍子。", "SEND_TEXT"),
    ("我想跟你说几句话，慢一点。", "SEND_TEXT"),
    ("我不想拍了，先用钢琴。", "SEND_TEXT"),
])
def test_only_standalone_first_person_announcements_open_another_take(words, expected):
    assert opening_mic.spoken_intent(words) == expected


def test_short_attack_cue_excludes_sustained_sounds_and_keeps_onset_times():
    rate = opening_mic.SAMPLE_RATE
    pcm = np.zeros(rate, dtype="<i2")
    for start in (rate // 4, rate // 2, 3 * rate // 4):
        pcm[start:start + rate // 100] = 8000
    assert "0.25, 0.5, 0.75" in opening_mic.listening_cue(pcm.tobytes())
    pcm[:rate // 5] = 8000
    assert opening_mic.listening_cue(pcm.tobytes()) is None


def test_silence_and_invalid_audio_work_without_an_audio_provider(monkeypatch):
    load_config = Mock(side_effect=opening_mic.LLMConfigurationError("missing"))
    monkeypatch.setattr(opening_mic.LLMConfig, "load", load_config)
    with closing(opening_mic.OpeningMic()) as mic:
        assert mic.recognize({"audio": recording(b"\0\0"), "mode": "intent"}) == {"route": "KEEP_LISTENING"}
        with pytest.raises(ValueError):
            mic.recognize({"audio": "broken", "mode": "intent"})
        load_config.assert_called_once()
        with pytest.raises(ValueError, match="配置不可用"):
            mic.recognize({"audio": recording(), "mode": "intent"})
