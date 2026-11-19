"""Local browser playback adapter for the realtime bar scheduler."""
from __future__ import annotations

import base64
import io
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from math import ceil, isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

import numpy as np
import soundfile as sf

from jammate.analysis.jam_capture import INPUT_MODES, analyze_capture
from jammate.contracts import BandPlan, InputEvent, PlanError
from jammate.contracts.adapters import from_text
from jammate.instruments import gm_program
from jammate.music import BEATS_PER_BAR
from jammate.orchestration.agent import SCOPES, RuleAgent, _clauses
from jammate.orchestration.addition_intent import asks_for_instruments
from jammate.orchestration.backend_arrangement import LLMBackendArranger, prepare_scoped_score, writable_layers
from jammate.orchestration.decision_gate import should_execute
from jammate.orchestration.explicit_request import explicit_request
from jammate.orchestration.key_intent import explicit_key_plan
from jammate.orchestration.progression_intent import asks_for_new_progression, explicit_progression_plan
from jammate.orchestration.tempo_intent import explicit_tempo
from jammate.orchestration.llm import LLMAgent, ProviderFailure, validate_plan
from jammate.orchestration.llm_config import LLMConfig, LLMConfigurationError
from jammate.orchestration.live_text import JEV_TIMEOUT, JevLiveTagger, LiveTextAgent
from jammate.orchestration.live_timing import LiveTurnTiming, can_wait, target_position
from jammate.orchestration.manager import StateManager
from jammate.orchestration.realtime import RealtimeSession, TimingPolicy
from jammate.orchestration.transitions import project
from jammate.render.synth import SR, TAIL_S, render
from jammate.render.mrt2 import generate_isolated
from jammate.render.soundfont import SoundFontRenderer
from jammate.render.runtime import create_runtime
from jammate.state import JamState


BUFFER_SECONDS = .62
VOICE_LIMIT = 2 * 1024 * 1024
TRANSCRIPTION_MODEL = "gpt-transcribe"
VOICE_FORMATS = {"audio/webm": "voice.webm", "audio/mp4": "voice.m4a",
                 "audio/ogg": "voice.ogg", "audio/wav": "voice.wav"}
JAM_VOCABULARY = ["鼓", "鼓点", "贝斯", "键盘", "主旋律", "吉他", "小节", "BPM", "力度", "密度", "静音"]
VOICE_ADDRESS = re.compile(r"^(?:(?:嘿|喂)\s*)?(?:乐队|Jam\s*Mate)(?![a-z])(?:[，,：:！!。\s]+|$)", re.I)
VOICE_LAYER_ACTIONS = {"build", "simplify", "add", "remove", "stop", "start"}
VOICE_POLITENESS = re.compile(r"^(?:请(?:你)?|麻烦(?:你)?|帮我)\s*|[吧呀啊呢]+(?=[。.!！?？]*$)")
VOICE_PARTS = "|".join(sorted((re.escape(name) for name, scope in SCOPES.items() if scope != "band"),
                              key=len, reverse=True))
VOICE_SCOPED_REQUEST = re.compile(
    rf"(?:把|让)?\s*(?:{VOICE_PARTS})\s*(?:再|稍微)?"
    r"(?:轻|小声|响|大声|少|稀|疏|密|重|松|紧)(?:一点|一些|点|些)"
    rf"|(?:把|让)?\s*(?:{VOICE_PARTS})\s*(?:小点声|大点声|来点反拍|打反拍|改成反拍)"
    rf"|(?:加入|加上|添加|加)\s*(?:一[把支个件架])?\s*(?:{VOICE_PARTS}|(?:中音|次中音|高音)?萨克斯)(?:进来)?",
    re.I,
)


def voice_command(text, state, *, accept_short=False):
    """Dispatch addressed speech and narrowly scoped unaddressed commands."""
    address = VOICE_ADDRESS.match(text)
    # Unpunctuated "乐队鼓少一点" needs the same request evidence as unnamed speech.
    content = text[address.end():].strip() if address else text.removeprefix("乐队").strip()
    normalized = VOICE_POLITENESS.sub("", content).strip()
    if not normalized or not any(character.isalnum() for character in normalized):
        return None
    event = from_text(normalized, session_id=state.session_id, source="speech")
    events = (event,)
    if (explicit_request(events, state=state) or explicit_tempo(events) or asks_for_instruments(events)
            or explicit_key_plan(events, state, "voice_gate") or asks_for_new_progression(events)
            or explicit_progression_plan(events, state, "voice_gate")):
        return normalized
    scoped = normalized[1:].lstrip() if normalized.startswith(("把", "让")) else normalized
    clauses = [(part, scope, action) for part, scope, action, _ in _clauses(scoped) if action != "keep"]
    if clauses and all(scope != "band" and action in VOICE_LAYER_ACTIONS for _, scope, action in clauses):
        return scoped
    if clauses and all((scope != "band" and action in VOICE_LAYER_ACTIONS) or VOICE_SCOPED_REQUEST.fullmatch(part)
                       for part, scope, action in clauses):
        return normalized
    if accept_short and len(clauses) == 1 and clauses[0][2] in {
        "faster", "slower", "start", "stop", "simplify", "build", "idea", "vary"
    }:
        return normalized
    return normalized if address else None


class BrowserJam:
    def __init__(self, bpm, make_agent, workers, *, soundfont=None, renderer="synth",
                 profile="electric", timbres=None, arrangement_config=None, decision_timeout=0., preview_lock=None):
        self.id = uuid4().hex
        self.manager = StateManager(JamState(session_id=self.id, tempo_bpm=bpm))
        self.renderer, self.profile, self.arrangement_config = renderer, profile, arrangement_config
        budget = arrangement_config.timeout_s if arrangement_config and renderer != "mrt2" else 0.
        self.decision_budget_samples = round((decision_timeout + budget) * SR)
        lookahead = max(3, ceil((decision_timeout + budget) * 180 / 240) + 3)
        self.runtime = RealtimeSession(self.manager, sample_rate=SR, timing=TimingPolicy(
            enqueue_guard_ms=750, preparation_budget_ms=max(350, round(budget * 1000)),
            lookahead_bars=lookahead))
        self.preview_lock = preview_lock or threading.Lock()
        self.preview = None
        self.lock = threading.Lock()
        self.make_agent, self.workers = make_agent, workers
        self.soundfont = soundfont
        self.timbres = self.next_timbres = self.reserved_timbres = self.timbres_applied_at = None
        if renderer == "fluidsynth":
            self.timbres = soundfont.profile_timbres(profile) if timbres is None else soundfont.validate_timbres(timbres)
        self.last_seen, self.closed = monotonic(), False
        self.reserved = self.packet = self.playing = None
        self.feedback = self.last_applied = self.deferred = self.future = None
        self.target = ("flexible", None)
        self.turn_timing = None
        self.transcribing = False

    def check_open(self):
        if self.closed:
            raise ValueError("这个 Jam 会话已经结束，请重新开始。")
        self.last_seen = monotonic()

    def reserve(self):
        bar = self.runtime.preview_next_bar()
        timing = bar.timing
        local_notes = tuple(note.shifted(-timing.bar_index * BEATS_PER_BAR) for note in bar.notes)
        duration = (timing.end_sample - timing.start_sample) / SR
        wave = io.BytesIO()
        timbres = self.timbres_for_bar(timing.bar_index)
        if self.renderer == "fluidsynth":
            audio = self.soundfont.render_all(local_notes, timing.tempo_bpm, SR, total_s=duration + TAIL_S,
                                              timbres=timbres)
        else:
            synthesize = self.soundfont.render if self.soundfont else render
            audio = synthesize(local_notes, timing.tempo_bpm, SR, total_s=duration + TAIL_S)
        sf.write(wave, audio, SR, format="WAV", subtype="PCM_16")
        self.reserved, self.reserved_timbres = bar, timbres
        self.packet = {
            "bar": timing.bar_index, "start": timing.start_sample, "end": timing.end_sample,
            "bpm": timing.tempo_bpm, "wav": base64.b64encode(wave.getvalue()).decode("ascii"),
        }

    def timbres_for_bar(self, bar):
        if self.next_timbres and self.next_timbres["bar"] <= bar:
            return self.next_timbres["programs"]
        if self.reserved is not None and self.reserved.timing.bar_index <= bar:
            return self.reserved_timbres
        return self.timbres

    def timbre_status(self):
        if self.timbres is None:
            return None
        pending = self.next_timbres
        if pending is None and self.reserved is not None and self.reserved_timbres != self.timbres:
            pending = {"bar": self.reserved.timing.bar_index, "programs": self.reserved_timbres}
        return {"active": self.timbres, "pending": pending, "applied_at": self.timbres_applied_at}

    def set_timbres(self, programs):
        with self.lock:
            self.check_open()
            if self.renderer != "fluidsynth":
                raise ValueError("换音色需要使用 FluidSynth。")
            requested = self.soundfont.validate_timbres(programs)
            if self.reserved is not None:
                existing, bar = self.reserved_timbres, self.reserved.timing.bar_index + 1
            else:
                existing, bar = self.timbres, self.manager.state.position.next_bar
            self.next_timbres = {"bar": bar, "programs": requested} if requested != existing else None
            return self.timbre_status()

    def snapshot(self, buffered=None):
        state = self.manager.state
        return {
            "session": self.id, "sample_rate": SR, "current_bar": self.playing.timing.bar_index if self.playing else None,
            "bpm": state.tempo_bpm, "playing": state.playing, "feedback": self.feedback,
            "last_applied": self.last_applied, "renderer": self.renderer,
            "preview_id": self.preview["id"] if self.preview else None, "timbres": self.timbre_status(),
            "layers": [{"name": layer.name, "active": layer.active, "density": layer.density,
                        **({"label": self.soundfont.programs[program]}
                           if self.soundfont and (program := gm_program(layer.name)) in self.soundfont.programs else {})}
                       for layer in state.layers if layer.name != "lead"],
            "next": self.packet if self.packet and self.packet["bar"] != buffered else None,
        }

    def poll(self, started, sample, buffered):
        if any(type(value) is not int for value in (started, sample, buffered)) or min(started, buffered) < -1 or sample < 0:
            raise ValueError("无效的浏览器播放位置。")
        with self.lock:
            self.check_open()
            latest = self.playing.timing.bar_index if self.playing else -1
            if started != latest:
                if not self.reserved or started != self.reserved.timing.bar_index:
                    raise ValueError("浏览器播放位置与待播小节不一致。")
                (bar,) = self.runtime.advance_to(self.reserved.timing.start_sample)
                if bar.timing != self.reserved.timing or bar.notes != self.reserved.notes:
                    raise RuntimeError("待播与确认的小节不同，请重新开始。")
                self.playing = bar
                if self.reserved_timbres != self.timbres:
                    self.timbres = self.reserved_timbres
                    self.timbres_applied_at = started
                if self.next_timbres and self.next_timbres["bar"] <= started:
                    self.next_timbres = None
                self.reserved = self.packet = self.reserved_timbres = None
                if bar.started is not None:
                    self.last_applied = {"bar": started, "plan": bar.started.plan_id}
                    if self.feedback and self.feedback.get("plan") == bar.started.plan_id:
                        self.feedback = {**self.feedback, "status": "applied"}
                if self.deferred is not None:
                    event, self.deferred = self.deferred, None
                    self.begin(event)
            if self.playing is not None:
                end = self.playing.timing.end_sample
                self.runtime.advance_to(max(self.runtime.sample, min(sample, end - 1)))
                self.refresh_wait()
                if self.reserved is None and (self.renderer == "mrt2"
                                              or end - self.runtime.sample <= round(SR * BUFFER_SECONDS)):
                    self.reserve()
            return self.snapshot(buffered)

    def submit(self, text, source="text"):
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError("请输入 1–500 字的演奏指令。")
        if source not in ("text", "speech"):
            raise ValueError("未知的指令来源。")
        return self.submit_event(from_text(text.strip(), session_id=self.id, source=source))

    def submit_event(self, event):
        if event.session_id != self.id or event.source not in {"text", "speech", "rhythm", "humming"}:
            raise ValueError("输入事件不属于这个 Jam 会话。")
        labels = {"rhythm": "拍手节奏", "humming": "哼唱旋律"}
        with self.lock:
            self.check_open()
            if self.feedback and self.feedback["id"] == event.event_id:
                return self.feedback
            label = labels.get(event.source) or event.payload["text"]
            current_bar = self.playing.timing.bar_index if self.playing else -1
            self.target = target_position(label, current_bar)
            self.turn_timing = None
            self.feedback = {"id": event.event_id, "text": label, "source": event.source, "status": "thinking"}
            if self.reserved is not None:
                # This buffer is already committed to the browser's playback
                # timeline. Apply new observations after its start is confirmed.
                self.deferred = event
                self.feedback = {**self.feedback, "status": "buffered"}
            else:
                self.begin(event)
            return self.feedback

    def begin(self, event):
        request = self.runtime.observe((event,))
        self.feedback = {**self.feedback, "status": "thinking"}
        if request is None:
            self.feedback = {**self.feedback, "status": "held", "message": "这段输入已处理。"}
            return
        if self.future is not None:
            self.future.cancel()
        position, bar = self.target
        slots = self.runtime.schedule_window()
        if position == "unsupported":
            self.feedback = {**self.feedback, "status": "held",
                             "message": "暂时只能指定下一小节。你也可以让计划准备好后生效。"}
            return
        if position == "next":
            slot = next((slot for slot in slots if slot.bar == bar), None)
            if slot is None:
                self.feedback = {**self.feedback, "status": "expired", "message": "已错过指定的下一小节。"}
                return
            limit = slot.enqueue_deadline_sample
            self.turn_timing = LiveTurnTiming(bar, limit, limit, monotonic() + (limit - self.runtime.sample) / SR, bar)
        else:
            soft = slots[min(2, len(slots) - 1)]
            hard = max(soft.enqueue_deadline_sample, self.runtime.sample + self.decision_budget_samples)
            self.turn_timing = LiveTurnTiming(soft.bar, soft.enqueue_deadline_sample - round(.350 * SR), hard,
                                              monotonic() + (hard - self.runtime.sample) / SR, None)
        self.future = self.workers.submit(self.decide, request, event.event_id)

    def is_current(self, event_id):
        return (not self.closed and self.feedback is not None and self.feedback["id"] == event_id
                and self.feedback["status"] != "expired")

    def refresh_wait(self):
        timing = self.turn_timing
        if (timing is None or self.renderer == "mrt2"
                or self.feedback["status"] not in {"thinking", "arranging", "waiting_ready"}):
            return
        if self.runtime.sample > timing.hard_sample or monotonic() > timing.hard_at:
            message = "已错过指定的下一小节。" if timing.target_bar is not None else "已超过本轮最长准备时间。"
            self.feedback = {**self.feedback, "status": "expired", "message": message}
            if self.future is not None:
                self.future.cancel()
        elif timing.target_bar is None and self.runtime.sample > timing.soft_sample:
            self.feedback = {**self.feedback, "status": "waiting_ready"}

    def bind_turn(self, request, plan, *, use_score, allow_wait):
        timing = self.turn_timing
        sent_bar = self.reserved.timing.bar_index if self.reserved else -1
        through = timing.target_bar
        if timing.target_bar is not None:
            sent_bar = max(sent_bar, timing.target_bar - 1)
        elif not allow_wait and self.renderer != "mrt2":
            through = timing.soft_bar
        return self.runtime.bind(request, plan, preparation_budget_ms=None if use_score else 350,
                                 after_bar=sent_bar, through_bar=through)

    def prepare_arrangement(self, state, plan, events):
        arranger = LLMBackendArranger(self.arrangement_config, self.renderer)
        if self.renderer == "synth":
            performance, _ = prepare_scoped_score(state, plan, events, arranger)
            return performance
        runtime = create_runtime(".", backend="fluidsynth", fluidsynth=self.soundfont.executable,
                                 soundfont=self.soundfont.soundfont, profile=self.profile,
                                 backend_arranger=arranger)
        with self.lock:
            timbres = self.timbres_for_bar(plan.to_dict()["effective_at"]["bar"])
        runtime.programs.update({1: timbres["keys"], 2: timbres["bass"]})
        performance, _ = runtime.prepare_execution(state, plan, events, lambda: None)
        return performance

    def render_preview(self, bound, event_id):
        validate_plan(bound.plan, bound.request.events, bound.state)
        with self.preview_lock, TemporaryDirectory(prefix="jammate-live-preview-") as directory:
            def check_current():
                with self.lock:
                    if not self.is_current(event_id):
                        raise PlanError("preview was superseded")
            check_current()
            arranger = (LLMBackendArranger(self.arrangement_config, "mrt2")
                        if self.arrangement_config else None)
            runtime = create_runtime(directory, backend="mrt2", allow_preview=True, backend_arranger=arranger,
                                     generator=generate_isolated)
            performance, conditioning = runtime.prepare_execution(
                bound.state, bound.plan, bound.request.events, check_current)
            check_current()
            runtime.export_audio(Path(directory), performance, conditioning)
            wav = base64.b64encode((Path(directory) / "band.wav").read_bytes()).decode("ascii")
            check_current()
            return {"id": event_id, "wav": wav,
                    "conditioning": conditioning.to_dict() if conditioning else None}

    def decide(self, request, event_id):
        try:
            agent = self.make_agent()
            plan = request.decide(agent)
            trace = getattr(agent, "last_trace", {})
            route = trace.get("source")
            direct = route in {"local_explicit", "explicit_music_request", "jev_direct"}
            allow_wait = not direct and can_wait(plan, request.state)
            with self.lock:
                self.refresh_wait()
                if not self.is_current(event_id):
                    return
                timing_label, confidence = trace.get("tags", {}).get("timing", ("flexible", 0))
                if timing_label == "fixed" and confidence >= .90 and self.turn_timing.target_bar is None:
                    self.feedback = {**self.feedback, "status": "held",
                                     "message": "需要明确生效位置，目前可以说「下一小节……」。"}
                    return
                use_score = (self.arrangement_config is not None and self.renderer != "mrt2"
                             and not direct
                             and bool(writable_layers(project(self.manager.state, plan), plan)))
                allow_wait = allow_wait or use_score
                data = plan.to_dict()
                if not should_execute(self.manager.state, plan, local_arrangement=use_score):
                    data["effective_at"] = {"bar": self.manager.state.position.next_bar, "beat": 0.}
                    self.manager.hold(BandPlan.from_dict(data))
                    self.feedback = {**self.feedback, "status": "held", "message": "；".join(data["unresolved"])}
                    return
                bound = self.bind_turn(request, plan, use_score=use_score, allow_wait=allow_wait)
                preview = self.renderer == "mrt2" and data["transport"].get("playing") is not False
                arrange = self.prepare_arrangement if use_score else None
                if (preview or arrange is not None) and self.feedback["status"] != "waiting_ready":
                    self.feedback = {**self.feedback, "status": "arranging"}
            if preview:
                result = self.render_preview(bound, event_id)
                with self.lock:
                    if self.is_current(event_id):
                        self.preview = result
                        self.feedback = {**self.feedback, "status": "preview", "message": data["reason"]}
                return
            for attempt in range(2):
                change = bound.prepare(arrange)
                with self.lock:
                    self.refresh_wait()
                    if not self.is_current(event_id):
                        return
                    try:
                        if self.reserved and bound.slot.bar <= self.reserved.timing.bar_index:
                            raise PlanError("enqueue deadline missed")
                        self.runtime.schedule(change)
                    except PlanError as error:
                        if (str(error) != "enqueue deadline missed" or attempt or not allow_wait
                                or self.turn_timing.target_bar is not None):
                            raise
                        bound = self.bind_turn(request, plan, use_score=use_score, allow_wait=True)
                        continue
                    self.feedback = {**self.feedback, "status": "scheduled", "bar": bound.slot.bar,
                                     "plan": plan.plan_id, "message": data["reason"], "decision_source": route,
                                     "timing": "when_ready" if bound.slot.bar > self.turn_timing.soft_bar else "prompt"}
                    return
        except (PlanError, ProviderFailure, LLMConfigurationError, ValueError, RuntimeError, OSError) as error:
            message = "这一轮没有可执行的计划。"
            if "deadline" in str(error):
                message = "指令来不及在可用小节生效。"
            elif isinstance(error, PlanError) and "编排失败" in str(error):
                message = str(error)
            elif self.renderer == "mrt2":
                message = "MRT2 试听未完成。"
            with self.lock:
                if self.is_current(event_id):
                    if "deadline" in str(error) and self.turn_timing and self.turn_timing.target_bar is not None:
                        message = "已错过指定的下一小节。"
                    self.feedback = {**self.feedback, "status": "held", "message": message}

    def close(self):
        with self.lock:
            self.closed = True
            if self.future is not None:
                self.future.cancel()


class BrowserJamService:
    def __init__(self, *, fluidsynth=None, soundfont=None):
        self.lock = threading.Lock()
        self.sessions = {}
        self.preview_lock = threading.Lock()
        self.workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jam-agent")
        self.soundfont = SoundFontRenderer(fluidsynth, soundfont) if fluidsynth and soundfont else None
        key = os.environ.get("JAMMATE_JEV_API_KEY") or os.environ.get("OHMYGPT_API_KEY")
        self.jev = JevLiveTagger(key) if key else None

    @staticmethod
    def config():
        if find_spec("openai") is None:
            raise LLMConfigurationError("未安装大模型依赖。")
        return LLMConfig.load()

    @staticmethod
    def recording(body):
        media_type = body.get("mime_type", "")
        encoded = body.get("audio")
        media_type = media_type.split(";", 1)[0].lower() if isinstance(media_type, str) else ""
        filename = VOICE_FORMATS.get(media_type)
        if filename is None or not isinstance(encoded, str) or len(encoded) > (VOICE_LIMIT + 2) // 3 * 4:
            raise ValueError("录音格式不受支持或录音太长，请缩短后重试。")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ValueError("录音数据无效，请重新录制。") from None
        if not audio or len(audio) > VOICE_LIMIT:
            raise ValueError("录音为空或太长，请重新录制。")
        return audio, media_type, filename

    def transcribe(self, session, recording, *, accept_short=False):
        audio, media_type, filename = recording
        config = self.config()
        from openai import APIError, APITimeoutError, OpenAI

        with session.lock:
            session.check_open()
            if session.transcribing:
                raise ValueError("上一条语音仍在识别。")
            session.transcribing = True
        try:
            with OpenAI(api_key=config.api_key, base_url=config.base_url, max_retries=0,
                        timeout=min(18, config.timeout_s)) as client:
                result = client.audio.transcriptions.create(
                    file=(filename, audio, media_type), model=TRANSCRIPTION_MODEL,
                    keywords=JAM_VOCABULARY,
                    prompt="这是 JamMate 乐队排练里对乐手说的话，按原话转写。",
                )
        except APITimeoutError:
            raise ValueError("语音识别超时，可以重录或改用文字。") from None
        except APIError:
            raise ValueError("语音识别服务暂时不可用，可以重录或改用文字。") from None
        finally:
            with session.lock:
                session.transcribing = False
        text = result.text.strip()
        with session.lock:
            session.check_open()
            command = (voice_command(text, session.manager.state, accept_short=accept_short)
                       if 0 < len(text) <= 500 else None)
        return {"command": command, "transcript": text[:500] or None,
                "recognition": "transcribed" if text else "no_speech"}

    def capture(self, session, body):
        mode = body.get("mode", "auto")
        if not isinstance(mode, str) or mode not in INPUT_MODES:
            raise ValueError("未知的麦克风输入类型。")
        recording = self.recording(body)
        if mode == "speech":
            return self.transcribe(session, recording, accept_short=True)
        if recording[1] != "audio/wav":
            raise ValueError("节奏和哼唱需要网页提供 WAV。")
        try:
            with sf.SoundFile(io.BytesIO(recording[0])) as stream:
                rate = stream.samplerate
                if not 8000 <= rate <= 96000 or stream.channels != 1 or not 0 < stream.frames <= 9 * rate:
                    raise ValueError("请使用 9 秒以内的单声道录音。")
                audio = stream.read(dtype="float32")
        except (sf.LibsndfileError, RuntimeError) as error:
            raise ValueError("无法读取麦克风录音。") from error
        if not np.isfinite(audio).all():
            raise ValueError("录音数据无效。")
        with session.lock:
            session.check_open()
            bpm = session.manager.state.tempo_bpm
        event, speech = analyze_capture(audio, rate, mode=mode, session_id=session.id, bpm=bpm)
        if event is not None:
            return {"event": event.to_dict()}
        if speech:
            try:
                self.config()
            except LLMConfigurationError:
                return {"command": None, "speech_unavailable": True}
            return self.transcribe(session, recording)
        recognition = {"rhythm": "no_rhythm", "humming": "no_melody", "auto": "unclassified"}
        return {"command": None, "recognition": recognition[mode]}

    @staticmethod
    def mrt2_available():
        return find_spec("magenta_rt") is not None

    def dispatch(self, operation, body):
        if operation == "status":
            try:
                self.config()
            except LLMConfigurationError:
                llm_ready = False
            else:
                llm_ready = True
            catalog = None
            if self.soundfont and self.soundfont.programs:
                catalog = {
                    "programs": self.soundfont.programs,
                    "presets": {name: self.soundfont.profile_timbres(name)
                                for name in ("electric", "acoustic", "synth")},
                }
            return {"llm_ready": llm_ready, "speech_ready": llm_ready, "speech_mode": "continuous",
                    "music_capture": True, "fluidsynth": self.soundfont is not None,
                    "timbre_catalog": catalog, "mrt2": self.mrt2_available()}
        if operation == "start":
            bpm, agent = body.get("bpm", 96), body.get("agent", "rule")
            renderer, arrangement = body.get("renderer", "synth"), body.get("arranger", "rule")
            profile = body.get("profile", "electric")
            timbres = body.get("timbres")
            if type(bpm) not in (float, int) or not isfinite(bpm) or not 60 <= bpm <= 180:
                raise ValueError("请选择 60–180 BPM。")
            if agent not in ("rule", "llm") or arrangement not in ("rule", "llm_all"):
                raise ValueError("未知的指令处理或编排方式。")
            if renderer not in ("synth", "fluidsynth", "mrt2") or profile not in ("electric", "acoustic", "synth"):
                raise ValueError("未知的音频后端或音色。")
            if renderer == "fluidsynth" and self.soundfont is None:
                raise ValueError("当前服务未配置 FluidSynth。")
            if renderer == "mrt2" and (not self.mrt2_available() or body.get("allow_preview") is not True):
                raise ValueError("MRT2 需要可用的模型环境和试听授权。")
            config = self.config() if agent == "llm" or arrangement == "llm_all" else None
            make_agent = RuleAgent
            if agent == "llm":
                programs = self.soundfont.programs if self.soundfont else None
                options = {"execution_validator": project} if arrangement == "llm_all" else {}
                make_agent = lambda: LiveTextAgent(
                    LLMAgent(config, fallback="error", soundfont_programs=programs,
                             arrangement_enabled=arrangement == "llm_all", **options), self.jev,
                )
            with self.lock:
                now = monotonic()
                for key, session in tuple(self.sessions.items()):
                    if now - session.last_seen > 90:
                        self.sessions.pop(key).close()
                if len(self.sessions) >= 8:
                    raise ValueError("会话已满，请先结束一个 Jam。")
                session = BrowserJam(bpm, make_agent, self.workers, soundfont=self.soundfont,
                                     renderer=renderer, profile=profile, timbres=timbres,
                                     arrangement_config=config if arrangement == "llm_all" else None,
                                     decision_timeout=config.timeout_s + (JEV_TIMEOUT if self.jev else 0.) if agent == "llm" else 0.,
                                     preview_lock=self.preview_lock)
                session.reserve()
                self.sessions[session.id] = session
                return session.snapshot()
        session_id = body["session"]
        if not isinstance(session_id, str):
            raise ValueError("会话标识无效。")
        with self.lock:
            session = self.sessions.pop(session_id, None) if operation == "stop" else self.sessions.get(session_id)
        if operation == "stop":
            if session is not None:
                session.close()
            return {"stopped": True}
        if session is None:
            raise ValueError("会话已结束，请重新开始 Jam。")
        if operation == "poll":
            return session.poll(body["started"], body["sample"], body["buffered"])
        if operation == "timbres":
            return {"timbres": session.set_timbres(body.get("programs"))}
        if operation == "feedback":
            if "event" in body:
                event = InputEvent.from_dict(body["event"])
                if event.source not in {"rhythm", "humming"}:
                    raise ValueError("这里只接收已识别的拍手或哼唱。")
                return {"feedback": session.submit_event(event)}
            return {"feedback": session.submit(body["text"], source=body.get("source", "text"))}
        if operation == "transcribe":
            return self.transcribe(session, self.recording(body))
        if operation == "capture":
            return self.capture(session, body)
        if operation == "preview":
            with session.lock:
                session.check_open()
                if not session.preview or session.preview["id"] != body.get("id"):
                    raise ValueError("试听已被替换。")
                return session.preview
        raise ValueError("未知的 Jam 操作。")

    def close(self):
        for session in self.sessions.values():
            session.close()
        self.workers.shutdown(wait=False, cancel_futures=True)
