import base64
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from jammate.xfyun_asr import (XfyunConfig, XfyunConfigurationError, XfyunRecognitionError,
                               XfyunTranscriber, words_from_result)


def result(sn, text):
    decoded = {"sn": sn, "ws": [{"cw": [{"w": word}]} for word in text]}
    return {"code": 0, "data": {"status": 2, "result": decoded}}


class Socket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []
        self.closed = False

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        return json.dumps(next(self.responses), ensure_ascii=False)

    def close(self):
        self.closed = True


def config():
    return XfyunConfig("appid", "key", "secret")


def test_authorization_uses_hmac_query_without_exposing_secret():
    client = XfyunTranscriber(config())
    url = client.authorization_url(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc))
    query = parse_qs(urlparse(url).query)
    authorization = base64.b64decode(query["authorization"][0]).decode()
    assert query["host"] == ["iat-api.xfyun.cn"]
    assert 'api_key="key"' in authorization and 'algorithm="hmac-sha256"' in authorization
    assert "secret" not in url and "secret" not in authorization


def test_transcriber_sends_v2_pcm_frames_and_decodes_words():
    socket = Socket([result(1, "鼓轻一点")])
    client = XfyunTranscriber(config(), websocket_factory=lambda url, timeout: socket)
    assert client.transcribe(b"\x01\x00" * 1000) == "鼓轻一点"
    assert [packet["data"]["status"] for packet in socket.sent] == [0, 1, 2]
    assert socket.sent[0]["business"]["domain"] == "iat"
    assert socket.sent[0]["data"]["format"] == "audio/L16;rate=16000"
    assert socket.sent[-1]["data"]["audio"] == ""
    assert socket.closed


def test_words_take_first_candidate():
    assert words_from_result({"ws": [{"cw": [{"w": "贝斯"}, {"w": "被撕"}]}]}) == "贝斯"


def test_config_requires_all_three_iflytek_credentials(tmp_path):
    path = tmp_path / "xfyun.json"
    path.write_text(json.dumps({"app_id": "", "api_key": "key", "api_secret": ""}), encoding="utf-8")
    with pytest.raises(XfyunConfigurationError, match="app_id、api_key 和 api_secret"):
        XfyunConfig.load(path)


def test_daily_quota_error_has_an_actionable_message():
    socket = Socket([{"code": 11201, "data": {"status": 2}, "sid": "redacted"}])
    client = XfyunTranscriber(config(), websocket_factory=lambda url, timeout: socket)
    with pytest.raises(XfyunRecognitionError, match="今日调用额度已用完"):
        client.transcribe(b"\x01\x00")
