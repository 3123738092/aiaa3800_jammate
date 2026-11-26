"""iFlytek streaming dictation WebSocket client for PCM recordings."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from time import sleep
from urllib.parse import urlencode
from urllib.parse import urlparse


class XfyunConfigurationError(ValueError):
    pass


class XfyunRecognitionError(RuntimeError):
    pass


ERROR_HINTS = {
    11200: "当前 APPID 未开通该服务、总额度已用完或授权已过期",
    11201: "当前 APPID 今日调用额度已用完",
    11202: "当前 APPID 触发秒级流控",
    11203: "当前 APPID 触发并发流控或授权已过期",
}


@dataclass(frozen=True)
class XfyunConfig:
    app_id: str
    api_key: str
    api_secret: str
    url: str = "wss://iat-api.xfyun.cn/v2/iat"
    timeout_s: float = 45.0

    @classmethod
    def load(cls, path: str | Path) -> "XfyunConfig":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise XfyunConfigurationError(f"无法读取讯飞配置：{path}") from exc
        required = ("app_id", "api_key", "api_secret")
        if not isinstance(data, dict) or any(not isinstance(data.get(key), str) or not data[key].strip()
                                             for key in required):
            raise XfyunConfigurationError("讯飞配置需要 app_id、api_key 和 api_secret。")
        return cls(**{key: data[key].strip() for key in required},
                   url=data.get("url", cls.url), timeout_s=float(data.get("timeout_s", 45)))


def words_from_result(result: dict) -> str:
    """Extract the first candidate from every word group in a decoded result."""
    return "".join(
        candidates[0].get("w", "")
        for group in result.get("ws", [])
        if isinstance(group, dict)
        and isinstance((candidates := group.get("cw")), list)
        and candidates and isinstance(candidates[0], dict)
    )


class XfyunTranscriber:
    FRAME_BYTES = 1280  # 40 ms of 16 kHz mono PCM16.

    def __init__(self, config: XfyunConfig, *, websocket_factory=None):
        self.config = config
        self._websocket_factory = websocket_factory

    def authorization_url(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        date = format_datetime(now.astimezone(timezone.utc), usegmt=True)
        parsed = urlparse(self.config.url)
        if parsed.scheme != "wss" or parsed.hostname != "iat-api.xfyun.cn" or parsed.path != "/v2/iat":
            raise XfyunConfigurationError("讯飞语音听写地址必须为 wss://iat-api.xfyun.cn/v2/iat。")
        host = parsed.hostname
        signature_origin = f"host: {host}\ndate: {date}\nGET {parsed.path} HTTP/1.1"
        digest = hmac.new(self.config.api_secret.encode(), signature_origin.encode(), hashlib.sha256).digest()
        signature = base64.b64encode(digest).decode()
        origin = (f'api_key="{self.config.api_key}", algorithm="hmac-sha256", '
                  f'headers="host date request-line", signature="{signature}"')
        authorization = base64.b64encode(origin.encode()).decode()
        return f"{self.config.url}?{urlencode({'host': host, 'date': date, 'authorization': authorization})}"

    def _connect(self):
        if self._websocket_factory is None:
            try:
                from websocket import create_connection
            except ImportError as exc:
                raise XfyunConfigurationError("请安装 websocket-client 以启用讯飞语音识别。") from exc
            factory = create_connection
        else:
            factory = self._websocket_factory
        return factory(self.authorization_url(), timeout=self.config.timeout_s)

    @staticmethod
    def _packet(app_id: str, chunk: bytes, status: int) -> dict:
        packet = {"data": {"status": status, "format": "audio/L16;rate=16000",
                           "encoding": "raw", "audio": base64.b64encode(chunk).decode()}}
        if status == 0:
            packet["common"] = {"app_id": app_id}
            packet["business"] = {"domain": "iat", "language": "zh_cn",
                                  "accent": "mandarin", "dwa": "wpgs"}
        return packet

    def transcribe(self, pcm: bytes) -> str:
        if not pcm or len(pcm) % 2:
            raise ValueError("讯飞输入必须是非空 PCM16 音频。")
        socket = self._connect()
        try:
            chunks = [pcm[i:i + self.FRAME_BYTES] for i in range(0, len(pcm), self.FRAME_BYTES)]
            for index, chunk in enumerate(chunks):
                status = 0 if index == 0 else 1
                socket.send(json.dumps(self._packet(self.config.app_id, chunk, status),
                                       ensure_ascii=False))
                sleep(0.04)
            socket.send(json.dumps(self._packet(self.config.app_id, b"", 2),
                                   ensure_ascii=False))

            segments: dict[int, str] = {}
            while True:
                response = json.loads(socket.recv())
                if response.get("code") != 0:
                    code = response.get("code")
                    hint = ERROR_HINTS.get(code, "服务返回业务错误")
                    raise XfyunRecognitionError(
                        f"讯飞识别失败：{hint}（code={code}，sid={response.get('sid', 'unknown')}）。")
                data = response.get("data") or {}
                result = data.get("result")
                if result:
                    text = words_from_result(result)
                    seq = int(result["sn"])
                    pgs = result.get("pgs")
                    replace_range = result.get("rg")
                    if pgs == "rpl" and isinstance(replace_range, list):
                        start, end = replace_range
                        for old in range(int(start), int(end) + 1):
                            segments.pop(old, None)
                    segments[seq] = text
                if data.get("status") == 2:
                    break
            return "".join(segments[key] for key in sorted(segments)).strip()
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise XfyunRecognitionError("讯飞返回了无法解析的识别结果。") from exc
        finally:
            socket.close()
