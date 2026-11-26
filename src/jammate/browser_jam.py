"""Local browser playback adapter for the realtime bar scheduler."""
from __future__ import annotations

import base64
import io
import os
import re
from random import choice
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from importlib.util import find_spec
from math import ceil, isfinite
from pathlib import Path
from platform import machine, system
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import uuid4

import numpy as np
import soundfile as sf

from jammate.analysis.humming_correction import correct_humming
from jammate.analysis.jam_capture import analyze_capture, route_capture
from jammate.analysis.rhythm import analyze_rhythm_audio, analyze_rhythm_feel
from jammate.contracts import BandPlan, InputEvent, PlanError
from jammate.contracts.adapters import from_humming, from_rhythm, from_system, from_text
from jammate.generate.arranger import chord_for_bar
from jammate.generate.harmony import harmony_cycle
from jammate.instruments import gm_program
from jammate.music import BEATS_PER_BAR
from jammate.orchestration.agent import SCOPES, RuleAgent, _clauses
from jammate.orchestration.addition_intent import asks_for_instruments
from jammate.orchestration.backend_arrangement import (
    LLMBackendArranger, MRTArrangement, prepare_scoped_score, writable_layers,
)
from jammate.orchestration.decision_gate import should_execute
from jammate.orchestration.explicit_request import explicit_request
from jammate.orchestration.jam_opening import cold_audio_plan, prepare_opening, text_beats_per_bar, text_settings
from jammate.orchestration.mrt2_intent import resolve_style, style_plan
from jammate.orchestration.key_intent import explicit_key_plan
from jammate.orchestration.progression_intent import asks_for_new_progression, explicit_progression_plan
from jammate.orchestration.tempo_intent import compile_jev_tempo, explicit_tempo
from jammate.orchestration.llm import LLMAgent, OpenAIProvider, ProviderFailure, validate_plan
from jammate.orchestration.llm_config import LLMConfig, LLMConfigurationError
from jammate.orchestration.live_text import JEV_TIMEOUT, JevLiveTagger, LiveTextAgent
from jammate.orchestration.live_timing import LiveTurnTiming, can_wait, target_position
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import prepare_bar, prepare_humming_answer, prepare_plan
from jammate.orchestration.plans import make_plan
from jammate.orchestration.realtime import RealtimeSession, TimingPolicy, prepare_conditioning
from jammate.orchestration.transitions import project
from jammate.orchestration.studio_suggestion import STUDIO_INTROS, accepted_plan, candidate
from jammate.render.midi_out import midi_program
from jammate.render.synth import SR, TAIL_S, render
from jammate.render.mrt2 import FRAME_RATE, generate_isolated
from jammate.render.soundfont import SoundFontRenderer
from jammate.render.runtime import create_runtime
from jammate.rhythm import RhythmContext
from jammate.state import JamState, MAX_TEMPO_BPM, MIN_TEMPO_BPM
from jammate.studio_input import midi_phrase, studio_text, with_studio_target


BUFFER_SECONDS = .62
MRT2_BUFFER_SECONDS = .28
MRT2_STREAM_RENDERERS = {"mrt2_stream", "mrt2_direct"}
VOICE_LIMIT = 2 * 1024 * 1024
DEBUG_HISTORY = 12
MAX_OPENING_SECONDS = 18  # Eight beats at 30 BPM, plus recording margin.
MAX_HUMMING_SECONDS = 20  # Up to 21 bars at 250 BPM.
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


def reference_event(event):
    data = event.to_dict()
    data["provenance"]["audio_ref"] = f"jam:{event.session_id}/{event.event_id}"
    return InputEvent.from_dict(data)


class BrowserJam:
    def __init__(self, bpm, make_agent, workers, *, soundfont=None, renderer="synth",
                 profile="electric", timbres=None, arrangement_config=None, decision_timeout=0., preview_lock=None,
                 opening=None, preview_workers=None, style_config=None, audio_references=None):
        if opening is None:
            state = JamState(session_id=uuid4().hex, tempo_bpm=bpm)
            events, selection = (), None
        else:
            state, events, selection = opening
        self.id = state.session_id
        self.direct_mrt2 = renderer == "mrt2_direct"
        self.style_config = style_config
        self.audio_references = audio_references
        self.manager = StateManager(state, follow_humming=True)
        opening_plan = None
        self.opening_answer = ()
        self.opening = None
        if selection is not None:
            self.manager.ingest(events)
            self.opening = {**selection, "input_events": [event.to_dict() for event in events]}
            humming = next((event for event in events if event.event_type == "musical_phrase"), None)
            if humming is not None and state.layer("lead").active:
                registered = self.manager.state
                motif = registered.motif(f"motif_{humming.event_id}")
                bars = ceil(max(note.end for note in motif.notes) / registered.beats_per_bar)
                opening_plan = make_plan(registered, events, f"opening_{self.id}", "RESPOND", [
                    {"action": "ECHO_MOTIF", "scope": "lead", "strength": .5, "parameters": {"motif_id": motif.motif_id}},
                ], [], {}, bars, selection["reason"], [])
                self.opening["melody_replay_plan"] = opening_plan.to_dict()
                if (selection.get("mode") == "humming" or selection.get("develop_humming")) and not self.direct_mrt2:
                    self.opening_answer = prepare_humming_answer(registered, motif)
                    self.opening["melody_continuation"] = {
                        "source_motif_id": motif.motif_id,
                        "start_beat": self.opening_answer[0].start,
                        "notes": [asdict(note) for note in self.opening_answer],
                    }
            elif selection.get("solo_voice") and not self.direct_mrt2:
                registered = self.manager.state
                opening_plan = make_plan(registered, events, f"opening_{self.id}", "INITIATE", [
                    {"action": "INTRODUCE_MOTIF", "scope": "lead", "strength": .5,
                     "parameters": {"motif_id": f"motif_opening_{self.id}"}},
                ], [], {}, 2, selection["reason"], [])
                self.opening["solo_plan"] = opening_plan.to_dict()
            if self.direct_mrt2:
                if opening_plan is None:
                    opening_plan = make_plan(self.manager.state, events, f"opening_{self.id}", "FOLLOW",
                                             [], [], {}, 1, selection["reason"], [])
                self.opening["mrt2_plan"] = opening_plan.to_dict()
        self.renderer, self.profile, self.arrangement_config = renderer, profile, arrangement_config
        self.mrt2_style = state.style or os.environ.get("JAMMATE_MRT2_PROMPT", "a warm instrumental band")
        self.mrt2_stream = None
        if renderer in MRT2_STREAM_RENDERERS:
            from mrt2_demo.browser_stream import BrowserMRT2Stream
            style = state.style or ("" if self.direct_mrt2 and selection else self.mrt2_style)
            settings = selection.get("settings", {}) if selection else {}
            self.mrt2_stream = BrowserMRT2Stream(style, os.environ.get("JAMMATE_MRT2_MODEL", "mrt2_small"),
                                                direct=self.direct_mrt2,
                                                tempo=settings.get("tempo_source") in {"manual", "text", "claps"},
                                                key=settings.get("key_source") in {"manual", "text"})
        budget = arrangement_config.timeout_s if arrangement_config and renderer not in {"mrt2", "mrt2_direct"} else 0.
        self.decision_budget_samples = round((decision_timeout + budget) * SR)
        lookahead = max(3, ceil((decision_timeout + budget) * MAX_TEMPO_BPM / (60 * state.beats_per_bar)) + 3)
        self.runtime = RealtimeSession(self.manager, sample_rate=SR, opening_plan=opening_plan,
                                       opening_answer=self.opening_answer, conditioning_only=self.direct_mrt2,
                                       timing=TimingPolicy(
            enqueue_guard_ms=350 if self.mrt2_stream is not None else 750,
            preparation_budget_ms=max(350, round(budget * 1000)),
            lookahead_bars=lookahead))
        self.preview_lock = preview_lock or threading.Lock()
        self.preview = self.opening_preview = self.opening_preview_future = None
        self.preview_workers = preview_workers or workers
        self.preview_job = self.preview_future = self.preview_status = None
        self.lock = threading.RLock()
        self.make_agent, self.workers = make_agent, workers
        self.soundfont = soundfont
        self.timbres = self.next_timbres = self.reserved_timbres = self.timbres_applied_at = None
        if renderer == "fluidsynth":
            self.timbres = soundfont.profile_timbres(profile) if timbres is None else soundfont.validate_timbres(timbres)
        self.last_seen, self.closed = monotonic(), False
        self.reserved = self.packet = self.playing = None
        self.reserved_output = self.playing_output = None
        self.feedback = self.last_applied = self.deferred = self.future = None
        self.studio_meter = None
        self.suggestion_offer = self.accepted_suggestion = None
        self.target = ("flexible", None)
        self.turn_timing = None
        self.debug_turns = {}
        self.debug_captures = deque(maxlen=DEBUG_HISTORY)

    def record_turn(self, event_id, **fields):
        # Called with the session lock, including by the decision worker.
        if event_id in self.debug_turns:
            self.debug_turns[event_id].update(fields)

    def update_feedback(self, **fields):
        self.feedback = {**self.feedback, **fields}
        self.record_turn(self.feedback["id"], feedback=self.feedback)

    @staticmethod
    def inspect_bar(bar, output):
        if bar is None:
            return None
        beat_count = bar.state.beats_per_bar if bar.state else BEATS_PER_BAR
        offset = bar.timing.bar_index * beat_count
        return {**asdict(bar.timing), "beats": beat_count, "output": output,
                **({"band_plan": bar.plan.to_dict()} if bar.plan else {}),
                "notes": [{**asdict(note), "start": note.start - offset} for note in bar.notes]}

    def debug(self):
        with self.lock:
            self.check_open()
            state = self.manager.state
            current, queued = self.playing, self.reserved
            current_output, queued_output = self.playing_output, self.reserved_output
            timbres, sample = self.timbre_status(), self.runtime.sample
            opening_preview = self.opening_preview
            captures = list(reversed(self.debug_captures))
            turns = [dict(row) for row in reversed(self.debug_turns.values())]
            for item in turns:
                plan = item.get("bound_plan")
                if plan and (status := self.manager.plan_status(plan["plan_id"])):
                    item["plan_status"] = status
        for item in turns:
            if request_state := item.pop("request_state", None):
                item["decision_state"] = request_state.to_dict()
            if applied_state := item.pop("applied_state", None):
                item["state_after_playback"] = applied_state.to_dict()
        cycle = harmony_cycle(state) if state.key else ()
        chord = chord_for_bar(state, current.timing.bar_index).name if cycle and current is not None else None
        return {
            "jam_state": state.to_dict(), "bpm": state.tempo_bpm, "key": state.key.name if state.key else None,
            "chord": chord, "progression": [chord.name for chord in cycle],
            "progression_start_bar": state.progression_start_bar,
            "current": self.inspect_bar(current, current_output), "queued": self.inspect_bar(queued, queued_output),
            "turns": turns, "captures": captures, "renderer": self.renderer, "timbres": timbres, "sample": sample,
            "mrt2_stream": self.mrt2_stream.diagnostics() if self.mrt2_stream else None,
            "runtime": self.manager.diagnostics(),
            "opening": {**self.opening, "mrt2_preview": opening_preview} if self.opening else None,
        }

    def record_capture(self, report):
        with self.lock:
            if not self.closed:
                self.debug_captures.append(report)

    def check_open(self):
        if self.closed:
            raise ValueError("这个 Jam 会话已经结束，请重新开始。")
        self.last_seen = monotonic()

    def reserve(self):
        bar = self.runtime.preview_next_bar()
        timing = bar.timing
        timbres = self.timbres_for_bar(timing.bar_index)
        packet = {"bar": timing.bar_index, "start": timing.start_sample, "end": timing.end_sample,
                  "bpm": timing.tempo_bpm}
        if self.mrt2_stream is not None:
            from mrt2_demo.inference import SAMPLE_RATE

            packet["pcm_frames"] = self.mrt2_stream.samples_for_bar(timing, SR)
            output = {"renderer": self.renderer, "format": "PCM16", "sample_rate": SAMPLE_RATE, "channels": 2,
                      "sample_frames": packet["pcm_frames"], "model": self.mrt2_stream.model}
        else:
            beat_count = bar.state.beats_per_bar if bar.state else BEATS_PER_BAR
            local_notes = tuple(note.shifted(-timing.bar_index * beat_count) for note in bar.notes)
            solo = self.opening.get("solo_voice") if self.opening else None
            if solo:
                local_notes = tuple(replace(note, instrument=solo) if note.instrument == "lead" else note
                                    for note in local_notes)
            duration = (timing.end_sample - timing.start_sample) / SR
            wave = io.BytesIO()
            if self.renderer == "fluidsynth":
                audio = self.soundfont.render_all(local_notes, timing.tempo_bpm, SR, total_s=duration + TAIL_S,
                                                  timbres=timbres)
            else:
                synthesize = self.soundfont.render if self.soundfont else render
                audio = synthesize(local_notes, timing.tempo_bpm, SR, total_s=duration + TAIL_S)
            sf.write(wave, audio, SR, format="WAV", subtype="PCM_16")
            packet["wav"] = base64.b64encode(wave.getvalue()).decode("ascii")
            tracks = []
            # The playback adapter can map a score's lead to a different timbre.
            pairs = tuple(dict.fromkeys((source.instrument, target.instrument)
                                        for source, target in zip(bar.notes, local_notes)))
            for source, target in pairs:
                external = self.renderer == "fluidsynth" or (self.soundfont is not None and gm_program(target) is not None)
                program = midi_program(target, timbres) if external and target != "drums" else None
                name = self.soundfont.programs.get(program) if program is not None else None
                tracks.append({"score_part": source, "instrument": target, "engine": "fluidsynth" if external else "synth",
                               "program": program, "timbre": name,
                               "notes": sum(note.instrument == source for note in bar.notes)})
            output = {"renderer": self.renderer, "format": "WAV PCM16", "sample_rate": SR,
                      "channels": audio.shape[1] if audio.ndim == 2 else 1, "sample_frames": len(audio), "tracks": tracks}
        self.reserved, self.reserved_timbres, self.packet, self.reserved_output = bar, timbres, packet, output

    def open_mrt2_bar(self, index):
        with self.lock:
            self.check_open()
            bar = self.reserved
            if self.mrt2_stream is None or bar is None or bar.timing.bar_index != index:
                raise ValueError("这个 MRT2 小节尚未预约或已失效。")
            reference = None
            if (self.audio_references and bar.plan is not None and bar.state is not None and bar.state.playing
                    and bar.state.interaction.allow_initiative):
                ids = bar.plan.to_dict()["trigger_event_ids"]
                reference = next(((event_id, *self.audio_references[event_id]) for event_id in reversed(ids)
                                  if event_id in self.audio_references), None)
            return self.mrt2_stream.open_bar(bar, SR, reference=reference)

    def keep_audio_reference(self, event, audio, rate):
        if self.audio_references is None or event.source != "humming":
            return event
        event = reference_event(event)
        with self.lock:
            self.check_open()
            self.audio_references[event.event_id] = (audio, rate)
            if len(self.audio_references) > 4:
                del self.audio_references[next(iter(self.audio_references))]
        return event

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
        cycle = harmony_cycle(state) if state.key else ()
        index = ((self.playing.timing.bar_index - state.progression_start_bar) % len(cycle)
                 if cycle and self.playing else None)
        return {
            "session": self.id, "sample_rate": SR, "current_bar": self.playing.timing.bar_index if self.playing else None,
            "bpm": state.tempo_bpm, "playing": state.playing, "feedback": self.feedback,
            "meter": self.studio_meter, "key": state.key.name if state.key else None,
            "chord": cycle[index].name if index is not None else None,
            "progression": [chord.name for chord in cycle] or None, "progression_index": index,
            "last_applied": self.last_applied, "renderer": self.renderer,
            "preview_id": self.preview["id"] if self.preview else None, "timbres": self.timbre_status(),
            "auto_preview_id": self.eligible_preview_id(), "preview_status": self.preview_status,
            "opening_preview": ({"status": self.opening_preview["status"],
                                 "error": self.opening_preview.get("error")} if self.opening_preview else None),
            "layers": [{"name": layer.name, "active": layer.active, "density": layer.density,
                        **({"label": self.soundfont.programs[program]}
                           if self.soundfont and (program := gm_program(layer.name)) in self.soundfont.programs else {})}
                       for layer in state.layers
                       if (layer.name != "lead" if self.opening is None else
                           layer.active or any(part["name"] == layer.name for part in self.opening["instruments"]))],
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
                if bar.timing != self.reserved.timing or bar.notes != self.reserved.notes or bar.plan != self.reserved.plan:
                    raise RuntimeError("待播与确认的小节不同，请重新开始。")
                self.playing = bar
                self.playing_output, self.reserved_output = self.reserved_output, None
                if self.reserved_timbres != self.timbres:
                    self.timbres = self.reserved_timbres
                    self.timbres_applied_at = started
                if self.next_timbres and self.next_timbres["bar"] <= started:
                    self.next_timbres = None
                self.reserved = self.packet = self.reserved_timbres = None
                if bar.started is not None:
                    self.last_applied = {"bar": started, "plan": bar.started.plan_id}
                    if self.opening and any(self.opening.get(key, {}).get("plan_id") == bar.started.plan_id
                                            for key in ("melody_replay_plan", "mrt2_plan", "solo_plan")):
                        self.opening = {**self.opening, "playback_receipt": asdict(bar.started)}
                    for event_id, row in self.debug_turns.items():
                        if row.get("bound_plan", {}).get("plan_id") == bar.started.plan_id:
                            self.record_turn(event_id, playback_receipt=asdict(bar.started), applied_state=self.manager.state)
                            if self.preview_job and self.preview_job[1].plan.plan_id == bar.started.plan_id:
                                job, self.preview_job = self.preview_job, None
                                if self.is_current(event_id) and self.manager.state.playing:
                                    self.preview_status = {"status": "generating", "event": event_id}
                                    self.preview_future = self.preview_workers.submit(self.finish_plan_preview, *job)
                            break
                    if self.feedback and self.feedback.get("plan") == bar.started.plan_id:
                        self.update_feedback(status="applied", playback={
                            "bar": started, "sample": bar.started.start_sample,
                            "mode": "mrt2_conditioning" if self.direct_mrt2 else "rendered_score",
                        })
                if self.deferred is not None:
                    batch, self.deferred = self.deferred, None
                    self.begin(*batch)
            if self.playing is not None:
                end = self.playing.timing.end_sample
                self.runtime.advance_to(max(self.runtime.sample, min(sample, end - 1)))
                self.refresh_wait()
                buffer_s = MRT2_BUFFER_SECONDS if self.mrt2_stream is not None else BUFFER_SECONDS
                if self.reserved is None and (self.renderer == "mrt2"
                                              or end - self.runtime.sample <= round(SR * buffer_s)):
                    self.reserve()
            return self.snapshot(buffered)

    def submit(self, text, source="text"):
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError("请输入 1–500 字的演奏指令。")
        if source not in ("text", "speech"):
            raise ValueError("未知的指令来源。")
        return self.submit_event(from_text(text.strip(), session_id=self.id, source=source))

    def offer_suggestion(self):
        with self.lock:
            self.check_open()
            if self.playing is None or self.feedback_in_progress():
                return None
            state = self.manager.state
            if not (proposal := candidate(state)):
                return None
            offer = self.suggestion_offer
            if not offer or offer["version"] != state.state_version or offer["expires"] <= monotonic():
                offer = {**proposal, "id": uuid4().hex, "version": state.state_version, "expires": monotonic() + 90}
                self.suggestion_offer = offer
            return {"id": offer["id"], "message": offer["message"], "kind": offer["kind"]}

    def feedback_in_progress(self):
        return self.feedback and self.feedback["status"] in {
            "buffered", "thinking", "waiting_ready", "arranging", "scheduled",
        }

    def accept_suggestion(self, offer_id):
        with self.lock:
            self.check_open()
            if isinstance(offer_id, str) and (saved := self.debug_turns.get(offer_id)):
                if saved.get("suggestion"):
                    return saved["feedback"]
            offer = self.suggestion_offer
            state = self.manager.state
            if (offer is None or offer["id"] != offer_id or offer["version"] != state.state_version
                    or offer["expires"] <= monotonic() or self.feedback_in_progress() or not candidate(state)):
                raise ValueError("这条提议已过期，请继续演奏。")
            event = from_system("accept_suggestion", session_id=self.id, event_id=offer_id)
            self.accepted_suggestion = offer
            return self.submit_event(event, suggestion=offer)

    def submit_event(self, event, *materials, suggestion=None):
        batch = (event, *materials)
        if any(item.session_id != self.id or (item.source not in {"text", "speech", "rhythm", "humming", "midi"}
                   and not (suggestion and item is event and item.source == "system"))
               for item in batch):
            raise ValueError("输入事件不属于这个 Jam 会话。")
        labels = {"rhythm": "拍手节奏", "humming": "哼唱旋律", "midi": "键盘旋律"}
        with self.lock:
            self.check_open()
            if saved := self.debug_turns.get(event.event_id):
                return saved["feedback"]
            self.suggestion_offer = None
            label = suggestion["message"] if suggestion else labels.get(event.source) or event.payload["text"]
            current_bar = self.playing.timing.bar_index if self.playing else -1
            self.target = target_position(label, current_bar)
            self.turn_timing = None
            if self.renderer == "mrt2":
                if self.preview_future is not None:
                    self.preview_future.cancel()
                self.preview_job = None
                self.preview_status = None
            if self.feedback and self.feedback["status"] in {"buffered", "thinking", "waiting_ready", "arranging"}:
                self.update_feedback(status="superseded")
            self.feedback = {"id": event.event_id, "text": label, "source": event.source, "status": "thinking"}
            self.debug_turns[event.event_id] = {"input_event": event.to_dict(), "feedback": self.feedback}
            if suggestion:
                self.debug_turns[event.event_id]["suggestion"] = {k: suggestion[k] for k in ("id", "kind", "message")}
            if materials:
                self.debug_turns[event.event_id]["input_events"] = [item.to_dict() for item in batch]
            if len(self.debug_turns) > DEBUG_HISTORY:
                del self.debug_turns[next(iter(self.debug_turns))]
            if self.reserved is not None:
                # This buffer is already committed to the browser's playback
                # timeline. Apply new observations after its start is confirmed.
                self.deferred = batch
                self.update_feedback(status="buffered")
            else:
                self.begin(*batch)
            return self.feedback

    def begin(self, event, *materials):
        request = self.runtime.observe((event, *materials))
        self.update_feedback(status="thinking")
        if request is None:
            self.update_feedback(status="held", message="这段输入已处理。")
            return
        self.record_turn(event.event_id, request_state=request.state)
        if self.future is not None:
            self.future.cancel()
        position, bar = self.target
        slots = self.runtime.schedule_window()
        if position == "unsupported":
            self.update_feedback(status="held", message="暂时只能指定下一小节。你也可以让计划准备好后生效。")
            return
        if position == "next":
            slot = next((slot for slot in slots if slot.bar == bar), None)
            if slot is None:
                self.update_feedback(status="expired", message="已错过指定的下一小节。")
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
            self.update_feedback(status="expired", message=message)
            if self.future is not None:
                self.future.cancel()
        elif timing.target_bar is None and self.runtime.sample > timing.soft_sample:
            self.update_feedback(status="waiting_ready")

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

    def prepare_arrangement(self, state, plan, events, arranger):
        if self.renderer in {"synth", "mrt2_stream"}:
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

    def start_opening_preview(self):
        """Compile the opening's actual notes before playback advances."""
        with self.lock:
            state = self.manager.state
            replay = self.opening.get("melody_replay_plan")
            performance = prepare_plan(state, BandPlan.from_dict(replay), opening_answer=self.opening_answer) if replay else None
            replay_bars = performance.bars if performance else 0
            bars = max(2, replay_bars)
            notes = (performance.notes if performance else ()) + tuple(
                note for bar in range(replay_bars, bars) for note in prepare_bar(state, bar)
            )
            if not notes:
                self.opening_preview = {"status": "waiting_material"}
                return
            frames_per_beat = 60 * FRAME_RATE / state.tempo_bpm
            duration = bars * state.beats_per_bar * 60 / state.tempo_bpm
            frames = ceil(duration * FRAME_RATE)
            pitched, drums = [], set()
            for note in notes:
                frame = min(frames - 1, round(note.start * frames_per_beat))
                if note.instrument == "drums":
                    drums.add(frame)
                else:
                    end = min(frames, ceil(note.end * frames_per_beat))
                    pitched.append({"pitch": note.pitch, "frame": frame, "length": max(1, end - frame)})
            from mrt2_demo.plan_conditioning import part_name
            parts = ", ".join(part_name(name) for name in sorted({note.instrument for note in notes}))
            style = state.style or parts
            if not pitched:
                style = "Solo drums only. No melody, bass, chords or harmonic instruments."
            conditions = MRTArrangement(style, tuple(pitched), tuple(
                {"frame": frame, "length": 1} for frame in sorted(drums)
            ))
            self.opening_preview = {"status": "generating", "conditioning": conditions.to_dict(),
                                    "duration_s": duration, "state_committed": False}
            self.opening_preview_future = self.preview_workers.submit(self.render_opening_preview, conditions, duration, frames)

    def render_opening_preview(self, conditions, duration, frames):
        try:
            with self.preview_lock, TemporaryDirectory(prefix="jammate-opening-preview-") as directory:
                with self.lock:
                    if self.closed:
                        return
                output = Path(directory) / "band.wav"
                runtime = create_runtime(directory, backend="mrt2", allow_preview=True, generator=generate_isolated)
                inference = runtime.generator(conditions.style, output=output, duration=duration,
                                              conditions=conditions.tokens(frames), backend=runtime.engine,
                                              model=runtime.model)
                wav = base64.b64encode(output.read_bytes()).decode("ascii")
                with self.lock:
                    if self.closed:
                        return
                    if self.preview is None:
                        self.preview = {"id": f"opening_{self.id}", "wav": wav,
                                        "conditioning": conditions.to_dict()}
                    self.opening_preview = {**self.opening_preview, "status": "completed", "inference": inference}
        except (ValueError, RuntimeError, OSError) as error:
            with self.lock:
                if not self.closed:
                    self.opening_preview = {**self.opening_preview, "status": "failed", "error": str(error)[:500]}

    def eligible_preview_id(self):
        if not self.preview or not self.manager.state.playing:
            return None
        expected = self.feedback["id"] if self.feedback else f"opening_{self.id}"
        return expected if self.preview["id"] == expected else None

    def finish_plan_preview(self, event_id, bound):
        arranger = (LLMBackendArranger(self.arrangement_config, self.renderer, capture_replies=True)
                    if self.arrangement_config is not None else None)
        try:
            preview = self.render_preview(bound, event_id, arranger)
            with self.lock:
                if self.is_current(event_id):
                    self.preview = preview
                    self.preview_status = {"status": "completed", "event": event_id}
        except (PlanError, ProviderFailure, LLMConfigurationError, ValueError, RuntimeError, OSError) as error:
            failure = {"type": type(error).__name__, "message": str(error)[:500]}
            with self.lock:
                self.record_turn(event_id, preview_error=failure)
                inference = self.debug_turns.get(event_id, {}).get("inference") or {}
                self.record_turn(event_id, inference={**inference, "status": "failed", "error": failure})
                if self.is_current(event_id):
                    self.preview_status = {"status": "failed", "event": event_id, "error": failure["message"]}
        finally:
            if arranger is not None:
                with self.lock:
                    self.record_turn(event_id, arrangement=arranger.last_trace)

    def render_preview(self, bound, event_id, arranger):
        validate_plan(bound.plan, bound.request.events, bound.state)
        with self.preview_lock, TemporaryDirectory(prefix="jammate-live-preview-") as directory:
            def check_current():
                with self.lock:
                    if not self.is_current(event_id):
                        raise PlanError("preview was superseded")
            check_current()
            runtime = create_runtime(directory, backend="mrt2", allow_preview=True, backend_arranger=arranger,
                                     generator=generate_isolated, prompt=self.mrt2_style)
            with self.lock:
                self.record_turn(event_id, inference={"model": runtime.model, "backend": runtime.engine,
                                                      "status": "preparing", "state_committed": False})
            performance, conditioning = runtime.prepare_execution(
                bound.state, bound.plan, bound.request.events, check_current)
            check_current()
            condition_data = conditioning.to_dict() if conditioning else None
            with self.lock:
                self.record_turn(event_id, conditioning=condition_data,
                                 inference={"model": runtime.model, "backend": runtime.engine, "status": "running",
                                            "state_committed": False})
            inference = runtime.export_audio(Path(directory), performance, conditioning)
            with self.lock:
                self.record_turn(event_id, inference={**inference, "status": "completed", "state_committed": False})
            wav = base64.b64encode((Path(directory) / "band.wav").read_bytes()).decode("ascii")
            check_current()
            return {"id": event_id, "wav": wav, "conditioning": condition_data}

    def decide(self, request, event_id):
        agent = arranger = None
        started = monotonic()
        try:
            plan, route = None, "cold_audio"
            proposal = self.accepted_suggestion
            if proposal and proposal["id"] == event_id:
                plan, route = accepted_plan(proposal, request.events, request.state), "studio_initiative"
            if plan is None and self.direct_mrt2:
                plan = style_plan(request.events, request.state, self.style_config)
                if plan is not None:
                    route = "mrt2_style"
                elif (request.state.playing and request.state.interaction.allow_initiative and self.audio_references
                      and any(event.event_id in self.audio_references for event in request.events)):
                    plan = make_plan(request.state, request.events, f"audio_{uuid4().hex}", "FOLLOW", [],
                                     ["tempo", "key", "progression", "groove", "playing"], {}, 1,
                                     "已将该录音安排为 MRT2 音频参考。", [])
                    route = "mrt2_audio_reference"
            if plan is None and (tempo := explicit_tempo(request.events)):
                plan = compile_jev_tempo(request.events, request.state, f"tempo_{uuid4().hex}",
                                         mode=tempo.mode, value=tempo.value)
                route = "local_explicit"
            plan = plan or cold_audio_plan(request)
            if plan is None:
                agent = self.make_agent()
                plan = request.decide(agent)
                trace = getattr(agent, "last_trace", {})
            else:
                trace = {"source": route}
            theme = request.state.accompaniment_motif
            if theme is not None and len(request.events) == 1 and request.events[0].event_type == "musical_phrase" \
                    and theme.motif_id == f"motif_{event_id}":
                # New humming supplies a new reference for the harmony layers.
                # Do not keep notes precomputed against a previous motif.
                data = plan.to_dict()
                harmonic_layers = {f"layer:{layer.name}" for layer in request.state.layers
                                   if layer.name not in {"drums", "lead"}}
                data["preserve"] = [item for item in data["preserve"] if item not in harmonic_layers]
                plan = BandPlan.from_dict(data)
            route = trace.get("source")
            direct = route in {"local_explicit", "explicit_music_request", "jev_direct", "cold_audio",
                               "mrt2_style", "mrt2_audio_reference", "studio_initiative"}
            allow_wait = not direct and can_wait(plan, request.state)
            with self.lock:
                self.record_turn(event_id, proposed_plan=plan.to_dict(), decision=trace)
                self.refresh_wait()
                if not self.is_current(event_id):
                    return
                timing_label, confidence = trace.get("tags", {}).get("timing", ("flexible", 0))
                if timing_label == "fixed" and confidence >= .90 and self.turn_timing.target_bar is None:
                    self.update_feedback(status="held", message="需要明确生效位置，目前可以说「下一小节……」。")
                    return
                use_score = (self.arrangement_config is not None and self.renderer not in {"mrt2", "mrt2_direct"}
                             and not direct
                             and bool(harmony_cycle(target := project(self.manager.state, plan)))
                             and bool(writable_layers(target, plan)))
                allow_wait = allow_wait or use_score
                data = plan.to_dict()
                if not should_execute(self.manager.state, plan, local_arrangement=use_score or self.direct_mrt2):
                    data["effective_at"] = {"bar": self.manager.state.position.next_bar, "beat": 0.}
                    self.manager.hold(BandPlan.from_dict(data))
                    self.record_turn(event_id, bound_plan=data)
                    self.update_feedback(status="held", message="；".join(data["unresolved"]))
                    return
                bound = self.bind_turn(request, plan, use_score=use_score, allow_wait=allow_wait)
                self.record_turn(event_id, bound_plan=bound.plan.to_dict(), slot=asdict(bound.slot))
                if use_score and self.feedback["status"] != "waiting_ready":
                    self.update_feedback(status="arranging")
            if use_score:
                backend = "synth" if self.mrt2_stream is not None else self.renderer
                arranger = LLMBackendArranger(self.arrangement_config, backend, capture_replies=True, retry_invalid=True)
            arrange = (lambda state, plan, events: self.prepare_arrangement(state, plan, events, arranger)) if use_score else None
            if self.direct_mrt2:
                arrange = prepare_conditioning
            for attempt in range(2):
                change = bound.prepare(arrange)
                with self.lock:
                    self.refresh_wait()
                    if not self.is_current(event_id):
                        return
                    try:
                        if self.reserved and bound.slot.bar <= self.reserved.timing.bar_index:
                            raise PlanError("enqueue deadline missed")
                        self.runtime.schedule(change, reuse_local_score=not use_score)
                    except PlanError as error:
                        if (str(error) != "enqueue deadline missed" or attempt or not allow_wait
                                or self.turn_timing.target_bar is not None):
                            raise
                        bound = self.bind_turn(request, plan, use_score=use_score, allow_wait=True)
                        self.record_turn(event_id, bound_plan=bound.plan.to_dict(), slot=asdict(bound.slot))
                        continue
                    keeps_material = bound.keeps_playing_material()
                    self.record_turn(event_id, renderer_input={
                        "kind": "conditioning" if self.direct_mrt2 else "score",
                        "arrangement": "llm" if use_score else "rule" if not self.direct_mrt2 else None,
                        "prepared_notes": len(change.performance.notes), "prepare_bars": change.performance.bars,
                        "keeps_material": keeps_material,
                    })
                    if self.renderer == "mrt2" and not keeps_material and project(bound.state, bound.plan).playing:
                        self.preview_job = (event_id, bound)
                    self.update_feedback(status="scheduled", bar=bound.slot.bar, plan=plan.plan_id,
                                         keeps_material=keeps_material,
                                         message=data["reason"], decision_source=route,
                                         timing="when_ready" if bound.slot.bar > self.turn_timing.soft_bar else "prompt")
                    return
        except (PlanError, ProviderFailure, LLMConfigurationError, ValueError, RuntimeError, OSError) as error:
            message = str(error) if self.direct_mrt2 and "风格" in str(error) else "这一轮没有可执行的计划。"
            if "deadline" in str(error):
                message = "指令来不及在可用小节生效。"
            elif isinstance(error, PlanError) and "编排失败" in str(error):
                message = str(error)
            elif self.renderer == "mrt2":
                message = "MRT2 试听未完成。"
            with self.lock:
                failure = {"type": type(error).__name__, "message": str(error)[:500]}
                self.record_turn(event_id, error=failure)
                inference = self.debug_turns.get(event_id, {}).get("inference")
                if inference and inference["status"] != "completed":
                    self.record_turn(event_id, inference={**inference, "status": "failed", "error": failure})
                if self.is_current(event_id):
                    if "deadline" in str(error) and self.turn_timing and self.turn_timing.target_bar is not None:
                        message = "已错过指定的下一小节。"
                    self.update_feedback(status="held", message=message)
        finally:
            with self.lock:
                trace = (getattr(agent, "last_trace", {}) if agent is not None else
                         self.debug_turns.get(event_id, {}).get("decision", {}))
                self.record_turn(event_id, decision=trace or {"source": "rule" if isinstance(agent, RuleAgent) else "unknown"},
                                 elapsed_ms=round((monotonic() - started) * 1000, 2))
                if arranger is not None:
                    self.record_turn(event_id, arrangement=arranger.last_trace)

    def close(self):
        with self.lock:
            self.closed = True
            if self.audio_references is not None:
                self.audio_references.clear()
            if self.mrt2_stream is not None:
                self.mrt2_stream.close()
            if self.future is not None:
                self.future.cancel()
            if self.opening_preview_future is not None:
                self.opening_preview_future.cancel()
            if self.preview_future is not None:
                self.preview_future.cancel()


class BrowserJamService:
    def __init__(self, *, fluidsynth=None, soundfont=None, audio_router=None):
        self.lock = threading.Lock()
        self.sessions = {}
        self.preview_lock = threading.Lock()
        self.workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jam-agent")
        self.preview_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jam-mrt2")
        self.soundfont = SoundFontRenderer(fluidsynth, soundfont) if fluidsynth and soundfont else None
        self.audio_router = audio_router
        key = os.environ.get("JAMMATE_JEV_API_KEY") or os.environ.get("OHMYGPT_API_KEY")
        self.jev = JevLiveTagger(key) if key else None

    @staticmethod
    def config():
        if find_spec("openai") is None:
            raise LLMConfigurationError("未安装大模型依赖。")
        return LLMConfig.load()

    def optional_config(self):
        try:
            return self.config()
        except LLMConfigurationError:
            return None

    @staticmethod
    def recording(body):
        if not isinstance(body, dict):
            raise ValueError("请提交录音。")
        media_type = body.get("mime_type", "")
        encoded = body.get("audio")
        media_type = media_type.split(";", 1)[0].lower() if isinstance(media_type, str) else ""
        if media_type != "audio/wav" or not isinstance(encoded, str) or len(encoded) > (VOICE_LIMIT + 2) // 3 * 4:
            raise ValueError("录音格式不受支持或录音太长，请缩短后重试。")
        try:
            audio = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ValueError("录音数据无效，请重新录制。") from None
        if not audio or len(audio) > VOICE_LIMIT:
            raise ValueError("录音为空或太长，请重新录制。")
        return audio

    def decode_recording(self, body, seconds):
        recording = self.recording(body)
        try:
            with sf.SoundFile(io.BytesIO(recording)) as stream:
                rate = stream.samplerate
                if not 8000 <= rate <= 96000 or stream.channels != 1 or not 0 < stream.frames <= seconds * rate:
                    raise ValueError(f"请使用 {seconds} 秒以内的单声道录音。")
                audio = stream.read(dtype="float32")
        except (sf.LibsndfileError, RuntimeError):
            raise ValueError("无法读取麦克风录音。") from None
        if not np.isfinite(audio).all():
            raise ValueError("录音数据无效。")
        return audio, rate

    def humming_provider(self):
        complete = getattr(self.audio_router, "complete_humming", None)
        return complete if complete and self.audio_router.ready else None

    def complete_humming(self, messages, schema, timeout_s):
        if complete := self.humming_provider():
            return complete(messages, schema, timeout_s)
        config = self.config()
        provider = OpenAIProvider(config, schema_name="jammate_humming_correction")
        return provider.complete(messages, schema, min(timeout_s, config.timeout_s))

    def browser_melody(self, document, session_id, max_seconds, *, correction=False):
        event = from_humming(document, session_id=session_id)
        melody = event.payload["melody"]
        if not .5 <= melody["source"]["durationSec"] <= max_seconds:
            raise ValueError(f"请提供 0.5–{max_seconds} 秒的哼唱。")
        if melody["selectedResult"] != "cleaned" or not melody["notes"]:
            raise ValueError("未识别到稳定的哼唱音符，请重新录制。")
        if correction:
            corrected = correct_humming(melody, self.complete_humming)
            return from_humming(corrected, session_id=session_id, event_id=event.event_id)
        return event

    def opening_events(self, body, *, bpm=None, audio_references=None, session_id=None, beats=None):
        if not isinstance(body, dict):
            raise ValueError("请提供至少一种开场素材：文字、拍手或哼唱。")
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > 500:
            raise ValueError("开场说明最多 500 字。")
        session_id = session_id or uuid4().hex
        target, source = body.get("target", "band"), body.get("source", "text")
        text_event = None
        if text.strip():
            text_event = (studio_text(text, target, session_id=session_id, source=source)
                          if "target" in body or "source" in body else from_text(text, session_id=session_id))
            if "studio_original" in body and isinstance(body["studio_original"], str):
                raw = text_event.to_dict()
                raw["provenance"]["original_text"] = body["studio_original"][:500]
                text_event = InputEvent(raw)
        beats_per_bar = beats or text_beats_per_bar(text_event)
        events = []
        if body.get("rhythm") is not None:
            clap, clap_rate = self.decode_recording(body["rhythm"], MAX_OPENING_SECONDS)
            timing = body.get("rhythm_timing", "strict")
            if timing not in {"strict", "feel"}:
                raise ValueError("请选择精确定速或节奏草图。")
            try:
                if timing == "feel":
                    text_bpm = text_settings(text_event)[0]
                    rhythm = analyze_rhythm_feel(clap, clap_rate, bpm or text_bpm or JamState.tempo_bpm, beats_per_bar)
                else:
                    rhythm = analyze_rhythm_audio(clap, clap_rate, beats_per_bar=beats_per_bar)
            except ValueError:
                if timing == "feel":
                    raise ValueError("节奏草图需要至少两个清晰起音。可手动指定 BPM。") from None
                raise ValueError("未能从拍手定速。请先均匀拍四下，再拍一整小节节奏，并录到小节结束。") from None
            if not rhythm.onset_beats:
                raise ValueError("定速后的一个小节没有识别到拍点，请重新录制拍手。")
            rhythm_event = from_rhythm(rhythm, session_id=session_id, audio_duration_s=len(clap) / clap_rate,
                                       tempo_source="detected" if timing == "strict" else "session",
                                       confidence_kind="grid_alignment_heuristic" if timing == "strict"
                                       else "feel_grid_heuristic")
            events.append(with_studio_target(rhythm_event, body["rhythm"]))
        if body.get("midi") is not None:
            if body.get("humming") is not None:
                raise ValueError("每次请提交一段有音高的素材：键盘旋律或哼唱。")
            events.append(midi_phrase(body["midi"], session_id=session_id))
        if body.get("humming") is not None:
            source = body["humming"]
            humming = None
            if isinstance(source, dict) and "melody" in source:
                melody = self.browser_melody(source["melody"], session_id, MAX_HUMMING_SECONDS,
                                             correction=source.get("correct_humming") is True)
            else:
                humming, humming_rate = self.decode_recording(source, MAX_HUMMING_SECONDS)
                melody, _ = analyze_capture(humming, humming_rate, mode="humming", session_id=session_id, bpm=None,
                                            allow_no_notes=audio_references is not None)
            if melody is None:
                raise ValueError("哼唱中没有识别到音符，请靠近麦克风重新录一小句。")
            if audio_references is not None and humming is not None:
                melody = reference_event(melody)
                audio_references[melody.event_id] = (humming, humming_rate)
            events.append(with_studio_target(melody, body["humming"]))
        if text_event:
            events.append(text_event)
        if not events:
            raise ValueError("请提供至少一种开场素材：文字、拍手或哼唱。")
        return tuple(events)

    @staticmethod
    def browser_transcript(session, body):
        text, mode = body.get("transcript"), body.get("mode", "auto")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 500 or mode not in ("auto", "speech"):
            raise ValueError("请提交浏览器转录的 1–500 字语音。")
        return BrowserJamService.speech_result(session, text, mode, engine="browser")

    @staticmethod
    def speech_result(session, text, mode, *, engine):
        text = text.strip()
        with session.lock:
            session.check_open()
            command = voice_command(text, session.manager.state, accept_short=mode == "speech") if len(text) <= 500 else None
        output = {"command": command, "transcript": text, "recognition": "transcribed"}
        session.record_capture({"stage": "transcribe", "engine": engine, "mode": mode, **output})
        return output

    def capture(self, session, body):
        mode = body.get("mode")
        if mode not in ("auto", "rhythm", "humming", "speech"):
            raise ValueError("请选择自动识别、语音、拍手或哼唱，再提交录音。")
        max_seconds = MAX_HUMMING_SECONDS if mode == "humming" else 9
        with session.lock:
            session.check_open()
            bpm = session.manager.state.tempo_bpm
            beats_per_bar = session.manager.state.beats_per_bar
        if "melody" in body:
            if mode not in {"humming", "auto"}:
                raise ValueError("请用哼唱或自动识别模式提交旋律。")
            event = self.browser_melody(body["melody"], session.id, max_seconds,
                                        correction=body.get("correct_humming") is True)
            data = event.to_dict()
            session.record_capture({"stage": "capture", "mode": mode, "engine": "basic-pitch", "event": data})
            return {"event": data}
        audio, rate = self.decode_recording(body, max_seconds)
        if mode == "auto" and body.get("humming_backend") == "basic-pitch":
            event, route = route_capture(audio, rate, mode=mode, session_id=session.id, bpm=bpm)
            if route == "humming":
                return {"recognition": "analyze_humming"}
            speech = route == "speech"
        else:
            event, speech = analyze_capture(audio, rate, mode=mode, session_id=session.id, bpm=bpm,
                                            allow_no_notes=session.audio_references is not None)
        if event is None and mode == "rhythm" and body.get("rhythm_timing") == "feel":
            try:
                context = analyze_rhythm_feel(audio, rate, bpm, beats_per_bar)
            except ValueError:
                pass
            else:
                event = from_rhythm(context, session_id=session.id, audio_duration_s=len(audio) / rate,
                                    tempo_source="session", confidence_kind="feel_grid_heuristic")
        if event is not None:
            event = session.keep_audio_reference(event, audio, rate)
            data = event.to_dict()
            session.record_capture({"stage": "capture", "mode": mode, "engine": "local", "event": data})
            return {"event": data}
        if speech and (mode == "speech" or (mode == "auto" and body.get("transcribe") is True)):
            if self.audio_router is None:
                raise ValueError("语音转写服务未配置。")
            try:
                result = self.audio_router.dictate(audio, rate)
            except ValueError as error:
                session.record_capture({"stage": "transcribe", "engine": "server_asr", "mode": mode,
                                        "recognition": "error", "error": str(error)})
                raise
            if result["route"] == "SEND_TEXT":
                return self.speech_result(session, result["instruction"], mode, engine="server_asr")
            session.record_capture({"stage": "transcribe", "engine": "server_asr", "mode": mode,
                                    "recognition": "no_speech"})
            return {"recognition": "no_speech"}
        outcomes = {"rhythm": "no_rhythm", "humming": "no_melody", "auto": "unclassified", "speech": "no_speech"}
        recognition = "browser_speech" if speech else outcomes[mode]
        session.record_capture({"stage": "capture", "mode": mode, "engine": "local", "recognition": recognition})
        return {"recognition": recognition}

    @staticmethod
    def mrt2_available():
        return find_spec("magenta_rt") is not None

    @staticmethod
    def mrt2_stream_available():
        return (system() == "Darwin" and machine() == "arm64"
                and all(find_spec(name) is not None for name in ("mrt2_demo", "magenta_rt", "mlx")))

    def open_mrt2_bar(self, session_id, bar):
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise ValueError("会话已结束，请重新开始 Jam。")
        return session.open_mrt2_bar(bar)

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
            stream_ready = self.mrt2_stream_available()
            speech_ready = bool(getattr(self.audio_router, "dictation_ready", False))
            return {"studio_v4": True, "llm_ready": llm_ready, "local_scoped_opening": True,
                    "speech_ready": speech_ready, "speech_mode": "server" if speech_ready else "browser",
                    "humming_correction_ready": bool(self.humming_provider()) or llm_ready,
                    "music_capture": True, "humming_clip_seconds": MAX_HUMMING_SECONDS,
                    "rhythm_opening_seconds": MAX_OPENING_SECONDS,
                    "fluidsynth": self.soundfont is not None,
                    "timbre_catalog": catalog, "mrt2": self.mrt2_available(), "opening_modes": ["warm", "cold", "humming"],
                    "cold_opening_settings": True, "cold_mrt2_preview": True, "mrt2_live_state": True,
                    "mrt2_stream": stream_ready, "mrt2_direct": stream_ready}
        if operation == "studio-classify":
            bpm = body.get("bpm", 100)
            if type(bpm) not in (int, float) or not MIN_TEMPO_BPM <= bpm <= MAX_TEMPO_BPM:
                raise ValueError("识别参考 BPM 无效。")
            audio, rate = self.decode_recording(body, MAX_HUMMING_SECONDS)
            # Match the requested bar length for short, unsteady clap sketches.
            session_id = uuid4().hex
            event, speech = analyze_capture(audio, rate, mode="auto", session_id=session_id, bpm=bpm)
            if event is None and not speech:
                try:
                    analyze_rhythm_feel(audio, rate, bpm, {"3/4": 3, "6/8": 2}.get(body.get("meter"), 4))
                except ValueError:
                    pass
                else:
                    return {"mode": "rhythm"}
            return {"mode": event.source if event else "speech" if speech else None}
        if operation == "studio-suggestion" and "session" not in body:
            if "accept" in body:
                raise ValueError("开场提议应通过开始会话接受。")
            idea = choice(tuple(STUDIO_INTROS))
            return {"offer": {"id": idea, "message": STUDIO_INTROS[idea]["message"], "kind": "opening"}}
        if operation == "start":
            agent = body.get("agent", "rule")
            mode = body.get("opening_mode", "warm")
            renderer, arrangement = body.get("renderer", "synth"), body.get("arranger", "rule")
            profile = body.get("profile", "electric")
            timbres = body.get("timbres")
            bpm_override = body.get("bpm")
            if bpm_override is not None and (type(bpm_override) not in (int, float) or not isfinite(bpm_override)
                                             or not MIN_TEMPO_BPM <= bpm_override <= MAX_TEMPO_BPM):
                raise ValueError(f"手动 BPM 需在 {MIN_TEMPO_BPM}–{MAX_TEMPO_BPM} 之间。")
            if agent not in ("rule", "llm") or arrangement not in ("rule", "llm_all"):
                raise ValueError("未知的指令处理或编排方式。")
            if mode not in ("warm", "cold", "humming"):
                raise ValueError("请选择冷启动、暖启动或自动补全哼唱。")
            if renderer not in {"synth", "fluidsynth", "mrt2", *MRT2_STREAM_RENDERERS} or profile not in ("electric", "acoustic", "synth"):
                raise ValueError("未知的音频后端或音色。")
            if renderer == "fluidsynth" and self.soundfont is None:
                raise ValueError("当前服务未配置 FluidSynth。")
            if renderer == "mrt2" and (not self.mrt2_available() or body.get("allow_preview") is not True):
                raise ValueError("MRT2 需要可用的模型环境和试听授权。")
            if renderer in MRT2_STREAM_RENDERERS and not self.mrt2_stream_available():
                raise ValueError("本地 MRT2 流式播放需要 Apple Silicon、MLX 和 MRT2 模型环境。")
            if renderer == "mrt2_direct":
                arrangement = "rule"
            opening = body.get("opening")
            meter = body.get("meter")
            idea_id = body.get("studio_idea")
            if idea_id is not None and (not isinstance(idea_id, str) or idea_id not in STUDIO_INTROS):
                raise ValueError("这个开场提议已失效。")
            if (idea_id is None and meter and isinstance(opening, dict) and isinstance(opening.get("text"), str)
                    and not any(opening.get(key) for key in ("midi", "humming", "rhythm"))
                    and opening.get("target", "band") in {"band", "drums"}
                    and re.fullmatch(r"\s*(?:请)?(?:你)?(?:给我)?(?:随便|随机)(?:来|给)?(?:一?个|一?段|点)?鼓点(?:吧)?[。！!]?\s*", opening["text"])):
                idea_id = choice(tuple(key for key, idea in STUDIO_INTROS.items() if idea["instrument"] == "drums"))
            idea = STUDIO_INTROS.get(idea_id)
            if idea:
                original = (opening or {}).get("text") or idea["message"]
                instruction = "只要鼓点" if idea["instrument"] == "drums" else "只要钢琴旋律"
                opening = {"text": instruction, "target": "band", "studio_original": original}
                mode, meter, bpm_override = "cold", idea["meter"], idea["bpm"]
            meters = {"4/4": 4, "3/4": 3, "6/8": 2}
            if meter is not None and meter not in meters:
                raise ValueError("请选择 4/4、3/4 或 6/8。")
            beats = meters.get(meter)
            audio_references = {} if renderer == "mrt2_direct" and body.get("mrt2_audio_reference") is True else None
            events = self.opening_events(opening, bpm=bpm_override, audio_references=audio_references, beats=beats)
            if agent == "llm" or arrangement == "llm_all":
                config = self.config()
            else:
                config = self.optional_config() if mode == "warm" and renderer != "mrt2_direct" else None
            programs = self.soundfont.programs if self.soundfont else {}
            state, selection = prepare_opening(events, config, programs, tempo_override=bpm_override,
                                               key_override=opening.get("key"), mode=mode, renderer=renderer, beats_override=beats)
            state = state.with_updates(beats_per_bar=beats or text_beats_per_bar(
                next((event for event in events if event.event_type == "speech"), None)))
            # Compound meter is two dotted beats. Provide a triplet groove for
            # the default backing; explicit input rhythm keeps its real onsets.
            if idea:
                state = state.with_updates(interaction=replace(state.interaction, allow_initiative=True))
                selection.update(suggestion={"id": idea_id, "message": idea["message"]},
                                 reason="以一小节鼓点开场。" if idea["instrument"] == "drums" else "先弹一句短旋律。")
            if idea and idea["instrument"] == "drums":
                context = RhythmContext(state.tempo_bpm, idea["onsets"], idea["accents"], beats_per_bar=beats)
                state = state.with_updates(rhythm=context)
                selection["settings"].update(rhythm_source="accepted_studio_idea",
                                             rhythm_beats_zero_based=list(context.onset_beats))
            elif (meter == "6/8" and state.layer("drums").active and opening.get("rhythm") is None
                    and not text_settings(next((event for event in events if event.event_type == "speech"), None))[2]):
                context = RhythmContext(state.tempo_bpm, tuple(step / 3 for step in range(6)),
                                        (1., .35, .4, .72, .35, .4), beats_per_bar=2)
                state = state.with_updates(rhythm=context)
                selection["settings"].update(rhythm_source="compound_meter",
                                             rhythm_beats_zero_based=list(context.onset_beats))
            if meter and not selection["instruments"] and opening.get("text") and not state.key:
                raise ValueError("本地规则需要明确的声部、和声或演奏素材才能开始。可使用大模型 Agent 理解开放式描述。")
            selection.setdefault("settings", {})["beats_per_bar"] = state.beats_per_bar
            if renderer in {*MRT2_STREAM_RENDERERS, "mrt2"}:
                text = next((event.payload["text"] for event in events if event.event_type == "speech"), "")
                style = selection.get("style_hint") or resolve_style(text, self.optional_config)
                if mode == "humming" or selection.get("develop_humming"):
                    style = ", ".join(filter(None, (style, "continue the hummed melody")))
                state = state.with_updates(style=style)
            make_agent = RuleAgent
            if agent == "llm":
                options = {"execution_validator": project} if arrangement == "llm_all" or renderer == "mrt2_direct" else {}
                make_agent = lambda: LiveTextAgent(
                    LLMAgent(config, fallback="error", soundfont_programs=programs,
                             arrangement_enabled=arrangement == "llm_all", capture_replies=True, **options), self.jev,
                )
            with self.lock:
                now = monotonic()
                for key, session in tuple(self.sessions.items()):
                    if now - session.last_seen > 90:
                        self.sessions.pop(key).close()
                if len(self.sessions) >= 8:
                    raise ValueError("会话已满，请先结束一个 Jam。")
                session = BrowserJam(state.tempo_bpm, make_agent, self.workers, soundfont=self.soundfont,
                                     renderer=renderer, profile=profile, timbres=timbres,
                                     arrangement_config=config if arrangement == "llm_all" else None,
                                     decision_timeout=config.timeout_s + (JEV_TIMEOUT if self.jev else 0.) if agent == "llm" else 0.,
                                     preview_lock=self.preview_lock, opening=(state, events, selection),
                                     preview_workers=self.preview_workers, style_config=self.optional_config,
                                     audio_references=audio_references)
                session.studio_meter = meter
                session.reserve()
                if mode in {"cold", "humming"} and renderer == "mrt2":
                    session.start_opening_preview()
                self.sessions[session.id] = session
                return {**session.snapshot(), "opening": {
                    "mode": mode, "instruments": selection["instruments"], "model": selection["model"],
                    "reason": selection["reason"], "key": state.key.name if state.key else None,
                    "settings": selection["settings"],
                    "progression": [chord.name for chord in harmony_cycle(state)],
                }}
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
        if operation == "debug":
            return session.debug()
        if operation == "timbres":
            return {"timbres": session.set_timbres(body.get("programs"))}
        if operation == "studio-suggestion":
            if "accept" in body:
                return {"feedback": session.accept_suggestion(body["accept"])}
            return {"offer": session.offer_suggestion()}
        if operation == "studio-input":
            refs = {} if session.audio_references is not None else None
            with session.lock:
                session.check_open()
                bpm, beats = session.manager.state.tempo_bpm, session.manager.state.beats_per_bar
            events = self.opening_events(body["input"], bpm=bpm, audio_references=refs, session_id=session.id, beats=beats)
            if refs:
                with session.lock:
                    session.check_open()
                    session.audio_references.update(refs)
            primary = next((event for event in events if event.event_type == "speech"), events[0])
            return {"feedback": session.submit_event(primary, *(event for event in events if event is not primary))}
        if operation == "feedback":
            if "event" in body:
                event = InputEvent.from_dict(body["event"])
                if event.source not in {"rhythm", "humming"}:
                    raise ValueError("这里只接收已识别的拍手或哼唱。")
                return {"feedback": session.submit_event(event)}
            return {"feedback": session.submit(body["text"], source=body.get("source", "text"))}
        if operation == "transcribe":
            return self.browser_transcript(session, body)
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
        self.preview_workers.shutdown(wait=False, cancel_futures=True)
