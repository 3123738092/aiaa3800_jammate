"""Generate shareable InputEvent fixtures; optionally preview the legacy bridge.

python examples/input_event_demo.py --preview
No microphone, LLM or cloud API is used. The humming source is a synthetic
fixture supplied with the 2026-10-24 handoff, not a personal recording.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jammate.contracts import load_input_event
from jammate.contracts.adapters import from_humming, from_rhythm, from_text, to_legacy_phrase, to_legacy_rhythm
from jammate.contracts.input_event import read_json
from jammate.rhythm import load_rhythm


def preview(events, output: Path) -> dict:
    import soundfile as sf
    from jammate.director.policy import decide
    from jammate.generate.arranger import arrange_bar, place_lead
    from jammate.render.midi_out import write_midi
    from jammate.render.synth import render
    from jammate.state import JamState

    # An explicit test session tempo, not the humming file's 120 BPM reference.
    state = JamState(tempo_bpm=100)
    rhythm_decision = decide(state, to_legacy_rhythm(events["rhythm"]), random.Random(3800))
    phrase = to_legacy_phrase(events["humming"], tempo_bpm=state.tempo_bpm)
    answer = decide(rhythm_decision.state, phrase, random.Random(3800))
    duration = events["humming"].payload["melody"]["source"]["durationSec"]
    start_bar = math.ceil(duration * state.tempo_bpm / 60 / 4)
    leads = place_lead(answer.lead, start_bar)
    bars = start_bar + math.ceil(max(n.end for n in answer.lead) / 4)
    notes = phrase.notes + tuple(n for bar in range(bars)
                                 for n in arrange_bar(answer.state, bar, leads.get(bar, ())))
    audio = render(notes, state.tempo_bpm)
    sf.write(output / "legacy_preview.wav", audio, 22050)
    write_midi(notes, state.tempo_bpm, output / "legacy_preview.mid")
    return {"kind": "legacy_compatibility_only", "session_bpm": state.tempo_bpm,
            "humming_note_count": len(phrase.notes), "leading_rest_beats": phrase.notes[0].start,
            "response_start_bar_zero_based": start_bar, "duration_s": len(audio) / 22050,
            "reason": answer.reason, "speech_executed": False,
            "limitations": "This legacy compatibility preview bypasses the new Agent; speech is preserved but not parsed here. Legacy policy normalizes the reply motif. Use agent_demo.py for the BandPlan loop. Synthetic playback, not live validation."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "demo_out" / "input_events")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    melody_path = ROOT / "examples" / "input_events" / "source" / "humming_melody_v1.json"
    events = {
        "speech": from_text("别停，鼓收一点，刚才那句保留。", session_id="jam_demo", event_id="evt_speech_001",
                            source="speech", started_at=12.0, ended_at=13.0, language="zh"),
        "rhythm": from_rhythm(load_rhythm(ROOT / "examples" / "rhythm_a.json"), session_id="jam_demo",
                              event_id="evt_rhythm_001", audio_duration_s=2.4, tempo_source="session",
                              confidence_kind="manual", started_at=12.0,
                              analysis_ref="examples/rhythm_a.json"),
        "humming": from_humming(read_json(melody_path), session_id="jam_demo", event_id="evt_humming_001",
                                started_at=20.0, analysis_ref="examples/input_events/source/humming_melody_v1.json"),
    }
    for name, event in events.items():
        assert load_input_event(event.write(args.out / f"{name}.json")) == event
    report = {"valid_events": [event.event_id for event in events.values()],
              "note": "Speech/rhythm timestamps overlap to illustrate independent event identity; this demo does not automatically group turns."}
    if args.preview:
        report["preview"] = preview(events, args.out)
    (args.out / "report.txt").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
