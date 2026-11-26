"""Alibaba Model Studio Qwen Audio ASR fallback for completed PCM recordings."""
from __future__ import annotations

import base64
import io
import os
import wave
from dataclasses import dataclass


MUSIC_VOCABULARY = (
    "JamMate, Hey JamMate, 节奏组, 旋律组, 全乐队, Bossa Nova, 波萨诺瓦, 爵士, 摇摆, 切分, 反拍, "
    "放克, Funk, Lo-fi, 鼓点, 底鼓, 军鼓, 踩镲, 贝斯, 钢琴, 吉他, 和弦, 三连音, "
    "BPM, 3/4 拍, 4/4 拍, 6/8 拍, 三拍子, 大调, 小调"
)


class QwenConfigurationError(ValueError):
    pass


class QwenRecognitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class QwenASRConfig:
    api_key: str
    model: str = "qwen-audio-3.1-asr-flash"
    endpoint: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    timeout_s: float = 60.0

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        key = environ.get("DASHSCOPE_API_KEY", "").strip()
        if not key:
            raise QwenConfigurationError("缺少 DASHSCOPE_API_KEY。")
        workspace = environ.get("DASHSCOPE_WORKSPACE_ID", "").strip()
        endpoint = environ.get("JAMMATE_QWEN_ASR_ENDPOINT", "").strip()
        if not endpoint and workspace:
            endpoint = (f"https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1/services/"
                        "aigc/multimodal-generation/generation")
        return cls(api_key=key, model=environ.get("JAMMATE_QWEN_ASR_MODEL", cls.model),
                   endpoint=endpoint or cls.endpoint,
                   timeout_s=float(environ.get("JAMMATE_QWEN_ASR_TIMEOUT", 60)))


def pcm_wave(pcm: bytes) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", ""))
        output.writeframes(pcm)
    return stream.getvalue()


class QwenASRTranscriber:
    def __init__(self, config: QwenASRConfig, *, session=None):
        self.config = config
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise QwenConfigurationError("请安装 requests 以启用百炼语音识别。") from exc
            session = requests.Session()
        self.session = session

    def transcribe(self, pcm: bytes) -> str:
        if not pcm or len(pcm) % 2:
            raise ValueError("百炼输入必须是非空 PCM16 音频。")
        encoded = base64.b64encode(pcm_wave(pcm)).decode()
        payload = {
            "model": self.config.model,
            "input": {"messages": [{"role": "user", "content": [{
                "type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{encoded}"},
            }]}]},
            "parameters": {"format": "wav", "sample_rate": "16000", "language_hints": ["zh", "en"]},
        }
        if self.config.model == "qwen-audio-3.1-asr-flash":
            payload["input"]["messages"].insert(0, {"role": "user", "content": [
                {"type": "input_text", "text": MUSIC_VOCABULARY},
            ]})
        try:
            response = self.session.post(
                self.config.endpoint,
                headers={"Authorization": f"Bearer {self.config.api_key}",
                         "Content-Type": "application/json", "X-DashScope-SSE": "disable"},
                json=payload, timeout=self.config.timeout_s,
            )
        except Exception as exc:
            raise QwenRecognitionError("百炼语音识别网络请求失败。") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise QwenRecognitionError(f"百炼语音识别返回非 JSON（HTTP {response.status_code}）。") from exc
        if response.status_code != 200:
            code = data.get("code", data.get("error", {}).get("code", "unknown"))
            request_id = data.get("request_id", response.headers.get("x-request-id", "unknown"))
            raise QwenRecognitionError(
                f"百炼语音识别失败（HTTP {response.status_code}，code={code}，request_id={request_id}）。")
        text = data.get("output", {}).get("text")
        if not isinstance(text, str):
            raise QwenRecognitionError("百炼语音识别响应缺少 output.text。")
        return text.strip()
