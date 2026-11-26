import json
from types import SimpleNamespace

import pytest

from jammate.qwen_asr import (QwenASRConfig, QwenASRTranscriber, QwenConfigurationError,
                              QwenRecognitionError, pcm_wave)


class Response:
    def __init__(self, status, data):
        self.status_code = status
        self.data = data
        self.headers = {}

    def json(self):
        return self.data


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_config_uses_existing_dashscope_environment_key():
    config = QwenASRConfig.from_environment({"DASHSCOPE_API_KEY": "secret"})
    assert config.api_key == "secret"
    assert config.model == "qwen-audio-3.1-asr-flash"
    with pytest.raises(QwenConfigurationError, match="DASHSCOPE_API_KEY"):
        QwenASRConfig.from_environment({})


def test_qwen_sends_inline_wav_and_returns_text():
    session = Session(Response(200, {"output": {"text": "鼓轻一点。"}, "request_id": "id"}))
    client = QwenASRTranscriber(QwenASRConfig("secret"), session=session)
    assert client.transcribe(b"\x01\x00" * 1600) == "鼓轻一点。"
    _, request = session.calls[0]
    assert request["headers"]["Authorization"] == "Bearer secret"
    assert request["headers"]["X-DashScope-SSE"] == "disable"
    assert request["json"]["model"] == "qwen-audio-3.1-asr-flash"
    context, utterance = request["json"]["input"]["messages"]
    assert "Bossa Nova" in context["content"][0]["text"]
    data = utterance["content"][0]["input_audio"]["data"]
    assert data.startswith("data:audio/wav;base64,")
    assert request["json"]["parameters"]["language_hints"] == ["zh", "en"]


def test_qwen_reports_http_failure_without_response_body_leak():
    session = Session(Response(429, {"code": "Throttling", "message": "sensitive detail"}))
    client = QwenASRTranscriber(QwenASRConfig("secret"), session=session)
    with pytest.raises(QwenRecognitionError, match="HTTP 429.*Throttling") as error:
        client.transcribe(b"\x01\x00")
    assert "sensitive detail" not in str(error.value)


def test_pcm_wave_has_wav_header():
    assert pcm_wave(b"\x00\x00" * 10).startswith(b"RIFF")
