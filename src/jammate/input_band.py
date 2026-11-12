"""Create a passage from one input through the team's versioned Agent loop."""
from __future__ import annotations

import base64
import io
import tempfile
from pathlib import Path
from uuid import uuid4

import soundfile as sf

from jammate.contracts import InputEvent
from jammate.contracts.adapters import from_text
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import OfflineRuntime
from jammate.render.midi_out import to_pretty_midi
from jammate.rhythm import RhythmMode
from jammate.state import JamState


def render_clip(document, *, creative=False, fluidsynth=None, soundfont=None):
    if creative and isinstance(document, dict) and set(document) == {"text"}:
        text = document["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError("请输入 1–500 字的创作提示。")
        event = from_text(text, session_id=f"creation_{uuid4().hex}")
    else:
        event = InputEvent.from_dict(document)
    payload = event.payload
    expected_strategy = "RESPOND"
    if event.source == "rhythm":
        tempo = payload["rhythm"]["tempo_bpm"]
        if payload["status"] != "ready" or payload["rhythm"]["confidence"] < 0.65:
            raise ValueError("拍点不足或与网格偏差过大，请重新拍一小节。")
    elif event.source == "humming":
        tempo = 100.0
        if not payload["melody"]["notes"]:
            raise ValueError("这段录音没有识别到音符，请重新哼唱或切换识别结果。")
    elif creative and event.source == "text":
        tempo = 100.0
        expected_strategy = "INITIATE"
    else:
        raise ValueError("此入口需要哼唱或节奏事件。")

    rhythm_mode = RhythmMode.VARY if creative and event.source == "rhythm" else RhythmMode.FOLLOW
    manager = StateManager(JamState(session_id=event.session_id, tempo_bpm=tempo, rhythm_mode=rhythm_mode))
    accepted = manager.ingest([event])
    state, history = manager.decision_snapshot()
    plan = RuleAgent().decide(accepted, state, plan_id=f"band_{uuid4().hex}", history=history, vary_input=creative)
    decision = plan.to_dict()
    if decision["unresolved"]:
        raise ValueError("指令未执行：" + "；".join(decision["unresolved"]))
    if decision["strategy"] != expected_strategy:
        if event.source == "text":
            raise ValueError("请用「来一段」开始创作，可以加上「快一点」或「慢一点」。")
        raise ValueError("乐队无法用这段输入生成回应，请尝试更短的乐句或更清晰的节奏。")
    manager.stage(plan)
    if fluidsynth is not None:
        from renderer_experiments.app.band_plan import KevinRuntime

        with tempfile.TemporaryDirectory() as directory:
            runtime = KevinRuntime(Path(directory), executable=fluidsynth, soundfont=soundfont)
            result = runtime.execute(manager, plan)
            wav = (runtime.last_artifacts / "band.wav").read_bytes()
            midi = (runtime.last_artifacts / "band.mid").read_bytes()
            warnings = runtime.last_receipt["persistence_warnings"]
        renderer = "Kevin / FluidSynth"
    else:
        result = OfflineRuntime().execute(manager, plan)
        wav_buffer, midi_buffer = io.BytesIO(), io.BytesIO()
        sf.write(wav_buffer, result.audio, result.sample_rate, format="WAV", subtype="PCM_16")
        to_pretty_midi(result.notes, result.tempo_bpm).write(midi_buffer)
        wav, midi = wav_buffer.getvalue(), midi_buffer.getvalue()
        renderer, warnings = "JamMate / NumPy", []
    return {
        "event_id": event.event_id, "plan": plan.to_dict(), "bars": result.bars,
        "tempo_bpm": result.tempo_bpm, "renderer": renderer, "agent": "rule",
        "warnings": warnings, "manager_diagnostics": manager.diagnostics(),
        "wav": base64.b64encode(wav).decode("ascii"), "midi": base64.b64encode(midi).decode("ascii"),
    }
