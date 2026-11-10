"""Web adapter executed with the pinned upstream packages on sys.path."""

from collections import OrderedDict
from contextlib import redirect_stdout
from dataclasses import dataclass, field
import base64
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import jammate
import numpy as np
import renderer_experiments.app
import soundfile as sf
from jammate.analysis.rhythm import analyze_rhythm_audio
from jammate.contracts import InputEvent
from jammate.contracts.adapters import from_rhythm, from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.llm import LLMAgent, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig, LLMConfigurationError
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.pipeline_rhythm import jam_rhythm
from jammate.render.midi_out import to_pretty_midi
from jammate.rhythm import RhythmContext, RhythmMode
from jammate.state import JamState


PROFILES = ("electric", "acoustic", "synth")
STYLES = ("lofi", "warm_funk", "indie_rock")


def encode(wav, midi):
    return {"wav": base64.b64encode(wav).decode("ascii"),
            "midi": base64.b64encode(midi).decode("ascii")}


def media(audio, sr, notes, bpm):
    wave, midi = io.BytesIO(), io.BytesIO()
    sf.write(wave, audio, sr, format="WAV", subtype="PCM_16")
    to_pretty_midi(notes, bpm).write(midi)
    return encode(wave.getvalue(), midi.getvalue())


def analyze(body):
    audio, sr = sf.read(io.BytesIO(base64.b64decode(body["wav"], validate=True)), dtype="float32", always_2d=True)
    if not 8000 <= sr <= 96000 or not .5 <= len(audio) / sr <= 30:
        raise ValueError("请使用 0.5–30 秒、8–96 kHz 的录音。")
    count_in = body["count_in"]
    context = analyze_rhythm_audio(np.mean(audio, axis=1), sr, count_in=count_in,
                                   tempo_hint=body["tempo"], beat_origin_s=body["origin"])
    return from_rhythm(context, session_id=body["session_id"], audio_duration_s=len(audio) / sr,
                       started_at=body["started_at"], tempo_source="detected" if count_in else "manual",
                       confidence_kind="grid_alignment_heuristic").to_dict()


def rhythm_clip(body):
    event = InputEvent.from_dict(body["event"])
    if event.event_type != "rhythm_seed":
        raise ValueError("节拍入口需要识别后的节拍。")
    bars, energy = body["bars"], body["energy"]
    if type(bars) is not int or not 1 <= bars <= 8 or type(energy) is not int or not 0 <= energy <= 3:
        raise ValueError("请选择 1–8 小节和 0–3 级力度。")
    context = RhythmContext(**event.payload["rhythm"])
    result = jam_rhythm(context, bars=bars, mode=RhythmMode(body["mode"]), energy=energy)
    return {"event_id": event.event_id, "mode": result.mode.value, "bars": bars,
            "tempo_bpm": context.tempo_bpm, "renderer": "原版节拍编排",
            **media(result.audio, result.sr, result.notes, context.tempo_bpm)}


@dataclass
class AppliedTurn:
    directory: TemporaryDirectory
    clip: object
    artifacts: Path | None
    result: dict


@dataclass
class TextWork:
    manager: StateManager
    settings: tuple
    pending: tuple | None = None
    applied: AppliedTurn | None = None
    completed: dict = field(default_factory=dict)
    recent: dict = field(default_factory=dict)


class ReferenceService:
    def __init__(self, fluidsynth, soundfont):
        self.fluidsynth, self.soundfont = fluidsynth, soundfont
        self.works = OrderedDict()

    def config(self):
        if importlib.util.find_spec("openai") is None:
            raise ValueError("大模型入口需要 OpenAI SDK。可先选用原版本地规则。")
        try:
            return LLMConfig.load()
        except LLMConfigurationError:
            raise ValueError("文字大模型尚未配置。可先选用原版本地规则。") from None

    def status(self):
        try:
            self.config()
        except ValueError as error:
            return {"llm_ready": False, "notice": str(error), "soundfont": bool(self.fluidsynth)}
        return {"llm_ready": True, "notice": "", "soundfont": bool(self.fluidsynth)}

    def text_turn(self, body):
        sid, rid, text = body["session_id"], body["request_id"], body["text"]
        if any(not isinstance(token, str) or not token.strip() or len(token) > 96 for token in (sid, rid)):
            raise ValueError("无效的作品或请求标识。")
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError("请输入 1–500 字的创作要求。")
        mode, bpm, profile = body["agent"], body["bpm"], body["profile"]
        after_turn = body["after_turn"]
        if mode not in ("llm", "rule") or profile not in PROFILES or type(after_turn) is not int or after_turn < 0:
            raise ValueError("请选择有效的 Agent 和音色。")
        if type(bpm) not in (float, int) or not 60 <= bpm <= 180:
            raise ValueError("起始速度需在 60–180 BPM。")
        if profile != "electric" and not self.fluidsynth:
            raise ValueError("选择音色需要本机 FluidSynth。")
        settings = (mode, bpm, profile)
        work = self.works.get(sid)
        if work is not None:
            if settings != work.settings:
                raise ValueError("修改初始设置前请开始新作品。")
            self.works.move_to_end(sid)
            if rid in work.completed:
                if work.completed[rid] != text:
                    raise ValueError("请求标识已使用。")
                if rid not in work.recent:
                    raise ValueError("这一轮已经完成，试听缓存已释放。")
                return work.recent[rid]
        elif after_turn:
            raise ValueError("这个作品会话已结束，请开始新作品。")
        if work is not None and after_turn != len(work.completed):
            raise ValueError("作品已生成更新的段落，请重试原请求或开始新作品。")
        if work is not None and work.applied is not None:
            if work.pending != (rid, text):
                raise ValueError("上一轮已执行。请保留上一条要求重试以取回文件，或开始新作品。")
            return self.deliver(work)

        config = self.config() if mode == "llm" else None
        if work is None:
            if len(self.works) >= 8:
                self.works.popitem(last=False)
            work = TextWork(StateManager(JamState(session_id=sid, tempo_bpm=bpm)), settings)
            self.works[sid] = work
        if len(work.completed) >= 16:
            raise ValueError("作品已生成 16 段，请下载结果后开始新作品。")

        manager = work.manager
        if work.pending is not None and work.pending[0] == rid:
            if work.pending[1] != text:
                raise ValueError("请求标识已使用。")
            accepted = manager.current_batch()
        else:
            accepted = manager.ingest([from_text(text, session_id=sid, event_id=rid)])
        if len(accepted) != 1 or accepted[0].event_id != rid:
            raise ValueError("此请求已被新的指令替换。")
        work.pending = (rid, text)

        snapshot, history = manager.decision_snapshot()
        directory = TemporaryDirectory(prefix="render-", ignore_cleanup_errors=True)
        try:
            runtime = self.runtime(directory.name, profile)
            agent = LLMAgent(config, fallback="error", execution_validator=runtime.validate) if config else RuleAgent()
            plan = agent.decide(accepted, snapshot, plan_id=f"web_{uuid4().hex}", history=history)
            decision = plan.to_dict()
            if not decision["actions"] and not decision["transport"]:
                raise ValueError("这条要求无法执行：" + "；".join(decision["unresolved"] or [decision["reason"]]))
            if not manager.stage(plan):
                raise ValueError("当前计划已失效，请重试。")
            rendered = runtime.execute(manager, plan)
        except Exception:
            directory.cleanup()
            raise
        result = {"session_id": sid, "request_id": rid, "turn": len(work.completed) + 1,
                  "tempo_bpm": rendered.tempo_bpm, "start_bar": rendered.start_bar, "bars": rendered.bars,
                  "plan": decision, "agent": mode, "renderer": "Kevin / FluidSynth" if self.fluidsynth else "原版合成器"}
        artifacts = runtime.last_artifacts if self.fluidsynth else None
        # The original runtime has already acknowledged the plan. Keep its
        # output available for retry without deciding or applying it again.
        work.applied = AppliedTurn(directory, rendered, artifacts, result)
        return self.deliver(work)

    @staticmethod
    def deliver(work):
        applied = work.applied
        rid, text = work.pending
        try:
            if applied.artifacts is not None:
                files = applied.artifacts
                output_media = encode((files / "band.wav").read_bytes(), (files / "band.mid").read_bytes())
            else:
                clip = applied.clip
                notes = tuple(note.shifted(-clip.start_bar * 4) for note in clip.notes)
                output_media = media(clip.audio, clip.sample_rate, notes, clip.tempo_bpm)
        except OSError:
            raise ValueError("这一轮已生成，但暂时无法读取音频。请保留原要求重试。") from None
        result = {**applied.result, **output_media}
        work.completed[rid] = text
        work.recent[rid] = result
        if len(work.recent) > 2:
            work.recent.pop(next(iter(work.recent)))
        work.pending = None
        work.applied = None
        applied.directory.cleanup()
        return result

    def runtime(self, output, profile):
        if self.fluidsynth:
            from renderer_experiments.app.band_plan import KevinRuntime
            return KevinRuntime(Path(output), executable=self.fluidsynth, soundfont=self.soundfont, profile=profile)
        return OfflineRuntime()

    def style_sample(self, body):
        from renderer_experiments.app.midi.files import render_with_fluidsynth, write_midi
        from renderer_experiments.app.midi.patterns import render_pattern
        from renderer_experiments.app.presets import get_instrument, get_style

        style = body["style"]
        if style not in STYLES or not self.fluidsynth:
            raise ValueError("请选择现成的风格预设，试听需要本机 FluidSynth。")
        intent, instrument = get_style(style), get_instrument("electric")
        bars = 2
        with TemporaryDirectory(prefix="style-") as output:
            midi, wav = Path(output) / "style.mid", Path(output) / "style.wav"
            write_midi(midi, render_pattern(intent, bars=bars), intent.tempo.bpm, instrument.programs,
                       duration_s=bars * 4 * 60 / intent.tempo.bpm)
            render_with_fluidsynth(midi, wav, self.fluidsynth, self.soundfont)
            return {"style": style, "bars": bars, "tempo_bpm": intent.tempo.bpm,
                    "renderer": "Kevin 风格实验", **encode(wav.read_bytes(), midi.read_bytes())}


def main(snapshot, fluidsynth, soundfont):
    root = Path(snapshot).resolve()
    for module in (jammate, renderer_experiments.app):
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise RuntimeError("reference worker must import the pinned snapshot")
    service = ReferenceService(Path(fluidsynth) if fluidsynth else None, Path(soundfont) if soundfont else None)
    routes = {"rhythm/analyze": analyze, "rhythm/create": rhythm_clip, "text/turn": service.text_turn,
              "text/status": lambda _: service.status(), "style": service.style_sample}
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with redirect_stdout(sys.stderr):
                reply = {"result": routes[request["operation"]](request["body"])}
        except ProviderFailure as error:
            reply = {"error": f"大模型请求未完成（{error.kind}）。请重试或改用本地规则。"}
        except subprocess.TimeoutExpired:
            reply = {"error": "音频渲染超时，请重试。"}
        except subprocess.SubprocessError:
            reply = {"error": "音频渲染未完成，请重试。"}
        except (ValueError, RuntimeError, KeyError, TypeError, OSError) as error:
            reply = {"error": str(error) or "请求未完成。"}
        print(json.dumps(reply, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
