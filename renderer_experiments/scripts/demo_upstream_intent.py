from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import wave
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.analysis import analyze_audio
from app.backends.lyria import LyriaRenderer
from app.midi.adapter import apply_render_overrides
from app.midi.files import render_with_fluidsynth, write_events, write_midi
from app.midi.patterns import render_pattern
from app.models import MusicIntent, Parts, Semantic
from app.presets import get_instrument
from app.upstream_intent_adapter import adapt_action_intent

SOURCE_PATH = ROOT / "examples/upstream-intent/configs/source_intent.json"
ROUND = ROOT / "results" / "generated-upstream-intent"
AUDIO, SESSIONS, ANALYSIS, CONFIGS = (ROUND / "audio", ROUND / "sessions", ROUND / "analysis", ROUND / "configs")
FLUIDSYNTH = ROOT / "tools/fluidsynth/fluidsynth-v2.6.1-win10-x64-cpp11/bin/fluidsynth.exe"
SOUNDFONT = ROOT / "tools/FluidR3_GM.sf2"

BASELINE = MusicIntent(
    semantic=Semantic(("funk",), ("warm", "groovy"), .7, .55, .55),
    parts=Parts(drums=.8, bass=0, harmony=.5),
    prompt="warm funk drum groove with electric piano comping",
)


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(pcm)


def safe_metrics(path: Path, intent: MusicIntent):
    if not path.is_file() or path.stat().st_size <= 44:
        return None
    data = analyze_audio(path)
    data["requested_bpm"] = intent.tempo.bpm
    data["bpm_error_raw"] = round(abs(data["estimated_bpm"] - intent.tempo.bpm), 3)
    return data


def event_signature(events, channel):
    return [(round(e.timestamp, 5), e.type, e.note, e.velocity) for e in events if e.channel == channel]


def run_midi_case(case_id, intent, overrides, source, decisions, unresolved, baseline_events=None):
    instrument = get_instrument("electric_performance")
    events = apply_render_overrides(render_pattern(intent, bars=8, arrangement="performance"), overrides)
    base = SESSIONS / case_id
    midi_path, events_path, wav_path = base.with_suffix(".mid"), base.with_suffix(".events.json"), AUDIO / f"{case_id}.wav"
    write_midi(midi_path, events, intent.tempo.bpm, instrument.programs)
    write_events(events_path, events)
    render_with_fluidsynth(midi_path, wav_path, FLUIDSYNTH, SOUNDFONT)
    result = {
        "experiment_id": case_id, "backend": "midi", "status": "passed", "source_intent": source,
        "baseline_state": BASELINE.to_dict(), "adapted_music_intent": intent.to_dict(),
        "adapter_decision_log": decisions, "unresolved_source_fields": unresolved,
        "backend_input": {"instrument_profile": {"name": instrument.name, "description": instrument.description, "programs": instrument.programs}, "arrangement": "performance", "midi_overrides": overrides},
        "transformation": {"drums": "performance arrangement; preserve-drum constraint checked by event signature", "bass": "soft -> bass part .45; velocity scale .58", "harmony": "same harmony and voice-led comping", "dreamy_mood": "higher keyboard/ornament reverb CC91"},
        "midi_path": str(midi_path), "events_path": str(events_path), "wav_path": str(wav_path),
        "objective_metrics": safe_metrics(wav_path, intent),
        "drum_events_preserved": event_signature(events, 9) == event_signature(baseline_events, 9) if baseline_events else None,
    }
    base.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result, events


async def run_lyria_update(source, baseline, adapted, decisions, unresolved):
    case_id = "lyria_upstream_intent_before_after_update"
    renderer = LyriaRenderer()
    result = {"experiment_id": case_id, "backend": "lyria", "status": "blocked", "source_intent": source, "baseline_state": baseline.to_dict(), "adapted_music_intent": adapted.to_dict(), "adapter_decision_log": decisions, "unresolved_source_fields": unresolved, "timing_policy": {"source_timing": "next_bar", "baseline_wait_seconds": 9.6, "interpretation": "update after four planned 100 BPM bars"}, "segments": []}
    try:
        await renderer.connect()
        await renderer.start(baseline)
        before = bytearray()
        async for chunk in renderer.receive(10.0): before.extend(chunk)
        before_path = AUDIO / f"{case_id}_before.wav"
        write_wav(before_path, bytes(before))
        result["segments"].append({"label": "before_update", "music_intent": baseline.to_dict(), "backend_input": renderer.backend_input(baseline), "audio_path": str(before_path), "objective_metrics": safe_metrics(before_path, baseline)})
        update_sent = time.monotonic()
        await renderer.update(adapted)
        after = bytearray()
        async for chunk in renderer.receive(14.0): after.extend(chunk)
        after_path = AUDIO / f"{case_id}_after.wav"
        write_wav(after_path, bytes(after))
        result["segments"].append({"label": "after_update", "music_intent": adapted.to_dict(), "backend_input": renderer.backend_input(adapted), "audio_path": str(after_path), "objective_metrics": safe_metrics(after_path, adapted), "update_sent_monotonic": update_sent})
        result.update(status="passed", renderer_metrics=renderer.metrics.as_dict())
    except Exception as exc:
        result.update(reason=f"{type(exc).__name__}: {exc}", renderer_metrics=renderer.metrics.as_dict())
    finally:
        try: await renderer.stop()
        except Exception: pass
    (SESSIONS / f"{case_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


async def main():
    load_dotenv(ROOT / ".env")
    if ROUND.exists(): shutil.rmtree(ROUND)
    for folder in (AUDIO, SESSIONS, ANALYSIS, CONFIGS): folder.mkdir(parents=True, exist_ok=True)
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    adapted = adapt_action_intent(source, BASELINE)
    (CONFIGS / "source_intent.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (CONFIGS / "adapter_result.json").write_text(json.dumps({"baseline_state": BASELINE.to_dict(), "adapted_music_intent": adapted.intent.to_dict(), "midi_overrides": adapted.midi_overrides, "decision_log": adapted.decision_log, "unresolved": adapted.unresolved}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    midi_base, base_events = run_midi_case("midi_upstream_intent_baseline", BASELINE, {"bass_velocity_scale": 1.0, "cc91_reverb": {1: 42, 3: 30}}, source, [], [], None)
    midi_adapted, _ = run_midi_case("midi_upstream_intent_adapted", adapted.intent, adapted.midi_overrides, source, adapted.decision_log, adapted.unresolved, base_events)
    lyria = await run_lyria_update(source, BASELINE, adapted.intent, adapted.decision_log, adapted.unresolved)
    (CONFIGS / "result_index.json").write_text(json.dumps({"midi_baseline": midi_base, "midi_adapted": midi_adapted, "lyria_update": lyria}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"midi_baseline": midi_base["status"], "midi_adapted": midi_adapted["status"], "lyria": lyria["status"]}, ensure_ascii=False))

if __name__ == "__main__": asyncio.run(main())
