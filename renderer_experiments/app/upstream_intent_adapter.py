from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .models import MusicIntent


@dataclass(frozen=True)
class AdapterResult:
    intent: MusicIntent
    midi_overrides: dict[str, Any]
    decision_log: list[dict[str, Any]]
    unresolved: list[str]


def adapt_action_intent(source: dict[str, Any], baseline: MusicIntent) -> AdapterResult:
    """Apply a rules-v1 action intent to a known renderer state.

    The source schema is a delta, so tempo/harmony/current parts must come from
    the caller's baseline state rather than being invented silently.
    """
    if source.get("status") != "ready":
        raise ValueError("source intent is not ready")

    intent = baseline
    decisions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    midi_overrides: dict[str, Any] = {
        "bass_velocity_scale": 1.0,
        "cc91_reverb": {1: 42, 3: 30},
        "preserve_drum_events": False,
        "bass_program_fallback": 33,
    }

    style_prompt = source.get("style_prompt")
    if style_prompt:
        prompt = f"{intent.prompt}; {style_prompt} atmosphere"
        mood = tuple(dict.fromkeys((*intent.semantic.mood, style_prompt)))
        intent = replace(intent.patch(semantic={"mood": mood}), prompt=prompt)
        decisions.append({
            "source": "style_prompt",
            "action": "append to mood and natural-language prompt",
            "value": style_prompt,
        })

    for action in source.get("actions", []):
        target, operation, parameter = action.get("target"), action.get("operation"), action.get("parameter")
        if target == "global" and operation == "set" and parameter == "mood":
            value = action.get("value")
            if isinstance(value, str) and value:
                mood = tuple(dict.fromkeys((*intent.semantic.mood, value)))
                intent = replace(intent.patch(semantic={"mood": mood}), prompt=f"{intent.prompt}; mood: {value}")
                decisions.append({"source": "actions.global.mood", "action": "merge into semantic.mood and prompt", "value": value})
        elif target == "bass" and operation == "add" and parameter == "instrument":
            dynamics = action.get("attributes", {}).get("dynamics")
            if dynamics == "soft":
                intent = replace(intent.patch(parts={"bass": 0.45}), prompt=f"{intent.prompt}; add a soft supportive bass")
                midi_overrides["bass_velocity_scale"] = 0.58
                midi_overrides["cc91_reverb"][1] = 68
                midi_overrides["cc91_reverb"][3] = 54
                decisions.append({
                    "source": "actions.bass.add.instrument",
                    "action": "enable bass at 0.45; scale Bass velocity to 0.58; raise keyboard reverb",
                    "timing": action.get("timing"),
                })
            else:
                unresolved.append("bass instrument action has unsupported or missing dynamics")
            if action.get("value") is None:
                unresolved.append("bass instrument value is null; MIDI uses Fingered Bass (GM 33) fallback")

    for constraint in source.get("constraints", []):
        if constraint.get("target") == "drums" and constraint.get("parameter") == "groove" and constraint.get("operation") == "preserve":
            midi_overrides["preserve_drum_events"] = True
            decisions.append({
                "source": "constraints.drums.groove",
                "action": "reuse identical drum event sequence from baseline MIDI render",
            })

    if "next_bar" not in {action.get("timing") for action in source.get("actions", [])}:
        unresolved.append("no bar-boundary timing was supplied for the bass action")

    return AdapterResult(intent, midi_overrides, decisions, unresolved)
