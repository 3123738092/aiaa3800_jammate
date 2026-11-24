"""Classify a completed opening take or transcribe dictated words using the audio router."""
import base64
import io
import json
import os
import re
from itertools import groupby
from pathlib import Path
from threading import Condition
import wave

import numpy as np
from scipy.signal import resample_poly

from jammate.analysis.jam_capture import isolated_attack_times
from jammate.orchestration.llm import OpenAIProvider, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig, LLMConfigurationError

SAMPLE_RATE = 16000
MAX_SECONDS = 30
ROUTES = ("WAIT_HUMMING", "WAIT_RHYTHM", "WAIT_TEXT", "USE_HUMMING", "USE_RHYTHM", "SEND_TEXT", "KEEP_LISTENING")
INTENT = """听这段音乐工作台开场录音，判断用户下一步想做什么。
WAIT_HUMMING：只预告自己要哼唱。WAIT_RHYTHM：只预告自己要拍打节奏。
WAIT_TEXT：只预告接下来想说几句、口述或提要求，还没有说出具体内容。
USE_HUMMING：已经有一串可以跟着哼的无词音高，哼声、口哨或电子音色的单旋律都可以作为素材。
USE_RHYTHM：已经听到一组短促的拍手、敲击或点击声，可以跟着它们打拍子。
录音可以从表演直接开始。即使没有人说话，只要旋律或拍点清楚，就使用这段录音。
SEND_TEXT：已经说出给乐队的要求（包括让乐队哼或拍）。instruction 填用户说的原话，
保留语言、数字和否定，不总结、不执行。预告后跟着具体要求时保留具体要求。
例如“我想拍几个拍子给你听”是 WAIT_RHYTHM，“你给我拍几个拍子”是 SEND_TEXT；
“我想跟你说几句话”是 WAIT_TEXT，“我想说，鼓轻一点”已给具体内容，是 SEND_TEXT。
噪音、沉默或无法判断时 KEEP_LISTENING。只有 SEND_TEXT 填 instruction，其余留空。"""
DICTATE = """你在给音乐工作台做听写。逐字转写用户口述，保留原语言、数字、否定、口语和约束，合理断句，不解释和执行。
同音字按音乐语境选择，例如调整快慢、加鼓、贝斯、钢琴，不添加录音没说的乐器或指令。
有可辨认的话就返回 SEND_TEXT 和转写原文，即使用户说的是录音指令。
没有可辨认的话时返回 KEEP_LISTENING 和空字符串。"""


def tonal_peaks(pcm):
    """Report successive sustained spectral peaks as listening cues, in Hz."""
    samples = np.frombuffer(pcm, dtype="<i2")
    size = SAMPLE_RATE // 4
    if len(samples) < size:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(samples, size)[::size // 2]
    power = np.abs(np.fft.rfft(frames * np.hanning(size), axis=1)) ** 2
    bins = power.argmax(axis=1)
    peaks = []
    for spectrum, index in zip(power, bins):
        hz = int(index) * SAMPLE_RATE // size
        tonal = 75 <= hz <= 1500 and spectrum[index-1:index+2].sum() > .45 * spectrum.sum()
        peaks.append(hz if tonal else None)
    sustained = [hz for hz, run in groupby(peaks) if hz and sum(1 for _ in run) >= 2]
    return sustained[:24] if len(set(sustained)) >= 2 else []


def listening_cue(pcm):
    if peaks := tonal_peaks(pcm):
        return (f"持续变化的频率（Hz）：{peaks}。结合音频，有明确说话时以原话为准。"
                "没有词语但有可跟哼的旋律时选择 USE_HUMMING。")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
    if attacks := isolated_attack_times(samples, SAMPLE_RATE):
        return (f"录音中测得多个彼此分开的短音，起始时间（秒）：{attacks[:32]}。"
                "结合音频，若听到成组拍点且没有说话，应使用这段节奏 USE_RHYTHM。"
                "只有说话预告才是 WAIT_RHYTHM，零散杂音仍是 KEEP_LISTENING。")
    return None


def spoken_intent(words):
    """Recover a bare announcement if the audio model transcribed it as a command."""
    sentence = re.sub(r"[\s，,。.!！?？]", "", words)
    prefix = r"我(?:们)?(?:现在)?(?:想要|想|要|打算|准备|来)(?:先|来)?"
    ending = r"(?:给你听听?|给你听一下|你听一下|试试)?(?:吧|啊)?"
    announcements = {
        "WAIT_HUMMING": r"(?:哼唱|哼|唱)(?:一下|一哼|一段|几句|一个)?(?:旋律|曲子|歌)?",
        "WAIT_RHYTHM": r"(?:拍手|拍|敲|打)(?:几个|几下|一段|一下|个)?(?:拍子|拍|节奏)?",
        "WAIT_TEXT": r"(?:(?:跟|和|对)你)?(?:说几句(?:话)?|说两句(?:话)?|说点话|口述一段)",
    }
    for route, content in announcements.items():
        if re.fullmatch(prefix + content + ending, sentence):
            return route
    return "SEND_TEXT"


class OpeningMic:
    """Own the shared audio provider for the lifetime of one Jam server."""

    def __init__(self):
        self._requests = Condition()
        self._active = 0
        self._closed = False
        self._provider = None
        try:
            self._provider = self._create_provider()
        except (LLMConfigurationError, OSError):
            # Offline Jam still works. A later recording can pick up a repaired config.
            pass

    @staticmethod
    def _create_provider():
        config_path = os.getenv("JAMMATE_AUDIO_ROUTER_CONFIG") or Path(__file__).resolve().parents[2] / "audio-router.secrets.json"
        config = LLMConfig.load(config_path, environ={})
        if config.api != "chat_completions":
            raise LLMConfigurationError("音频入口需要 chat_completions。")
        return OpenAIProvider(config, schema_name="jammate_opening_mic")

    def _complete(self, messages, schema, *, schema_name=None, timeout_s=None):
        with self._requests:
            if self._closed:
                raise RuntimeError("总麦克风服务已关闭。")
            if self._provider is None:
                self._provider = self._create_provider()
            provider = self._provider
            self._active += 1
        try:
            if schema_name is not None:
                provider = OpenAIProvider(provider.config, client=provider.client, schema_name=schema_name)
            timeout = provider.config.timeout_s
            return provider.complete(messages, schema, min(timeout, timeout_s) if timeout_s is not None else timeout)
        finally:
            with self._requests:
                self._active -= 1
                if not self._active:
                    self._requests.notify_all()

    @property
    def ready(self):
        with self._requests:
            return self._provider is not None and not self._closed

    def complete_humming(self, messages, schema, timeout_s):
        return self._complete(messages, schema, schema_name="jammate_humming_correction", timeout_s=timeout_s)

    def dictate(self, audio, rate):
        """Convert a live mono segment to the same 16 kHz payload as the opening mic."""
        if rate != SAMPLE_RATE:
            divisor = np.gcd(rate, SAMPLE_RATE)
            audio = resample_poly(audio, SAMPLE_RATE // divisor, rate // divisor)
        pcm = np.rint(np.clip(audio, -1., 1.) * 32767).astype("<i2")
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wav:
                wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                wav.writeframes(pcm.tobytes())
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return self.recognize({"mode": "dictation", "audio": encoded})

    def close(self):
        with self._requests:
            self._closed = True
            self._requests.wait_for(lambda: not self._active)
            if self._provider is not None:
                self._provider.client.close()
                self._provider = None

    def recognize(self, body):
        mode = body.get("mode")
        encoded = body.get("audio")
        if mode not in ("intent", "dictation") or not isinstance(encoded, str) or len(encoded) > 1_300_000:
            raise ValueError("请提交 30 秒以内的麦克风录音。")
        try:
            raw = base64.b64decode(encoded, validate=True)
            with wave.open(io.BytesIO(raw)) as wav:
                frames = wav.getnframes()
                if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1, SAMPLE_RATE, 2):
                    raise ValueError("请使用 16 kHz 单声道 PCM 录音。")
                if not 0 < frames <= MAX_SECONDS * SAMPLE_RATE:
                    raise ValueError("请录制 30 秒以内的音频。")
                pcm = wav.readframes(frames)
                if len(pcm) != frames * 2:
                    raise ValueError("录音不完整，请重录。")
        except (EOFError, wave.Error) as exc:
            raise ValueError("无法读取录音，请重录。") from exc
        if not any(pcm):
            return {"route": "KEEP_LISTENING"}

        allowed = ROUTES if mode == "intent" else ("SEND_TEXT", "KEEP_LISTENING")
        schema = {"type": "object", "properties": {"route": {"type": "string", "enum": list(allowed)},
                  "instruction": {"type": "string"}}, "required": ["route", "instruction"], "additionalProperties": False}
        parts = [{"type": "input_audio", "input_audio": {"format": "wav", "data": encoded}}]
        if mode == "intent" and (cue := listening_cue(pcm)):
            parts.append({"type": "text", "text": cue})
        try:
            reply = self._complete([{"role": "system", "content": INTENT if mode == "intent" else DICTATE},
                                    {"role": "user", "content": parts}], schema)
        except LLMConfigurationError as exc:
            raise ValueError("总麦克风服务配置不可用。") from exc
        except ProviderFailure as exc:
            raise ValueError(f"录音识别服务暂不可用（{exc.kind}），请重试。") from exc
        try:
            decision = json.loads(reply.text)
            route, words = decision["route"], decision["instruction"].strip()
            if route not in allowed or bool(words) != (route == "SEND_TEXT"):
                raise ValueError()
        except (TypeError, ValueError, AttributeError, KeyError) as exc:
            raise ValueError("录音识别结果无效，请再试一次。") from exc
        if mode == "intent" and route == "SEND_TEXT":
            route = spoken_intent(words)
        return {"route": route, "instruction": words} if route == "SEND_TEXT" else {"route": route}
