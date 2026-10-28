"""Perception boundary. Seconds refer to a session clock, never MIDI tempo."""
from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4


def finite(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    return float(value)


def validate_event(event: dict) -> dict:
    if not isinstance(event, dict):
        raise ValueError("InputEvent must be an object")
    for name in ("event_id", "session_id"):
        if not isinstance(event.get(name), str) or not event[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    pairs = {"humming": "musical_phrase", "rhythm": "rhythm_seed", "speech": "speech"}
    if not isinstance(event.get("source"), str) or event["source"] not in pairs or event.get("event_type") != pairs[event["source"]]:
        raise ValueError("unsupported source/event_type pair")
    start = finite(event.get("started_at"), "started_at")
    end = finite(event.get("ended_at"), "ended_at")
    if end <= start:
        raise ValueError("ended_at must be after started_at")
    if "confidence" not in event:
        raise ValueError("confidence is required (null means uncalibrated/unknown)")
    if event["confidence"] is not None and finite(event["confidence"], "confidence") > 1:
        raise ValueError("confidence must be in [0, 1] or null")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if "timebase" in payload and payload["timebase"] != "seconds_from_recording_start":
        raise ValueError("unsupported payload timebase")
    if event["source"] == "humming":
        if not isinstance(payload.get("notes"), list):
            raise ValueError("humming payload requires notes")
        for note in payload["notes"]:
            if not isinstance(note, dict):
                raise ValueError("each note must be an object")
            pitch = finite(note.get("pitch_midi"), "pitch_midi")
            if not pitch.is_integer() or pitch > 127:
                raise ValueError("pitch_midi must be an integer within 0-127")
            time = finite(note.get("start_sec"), "note.start_sec")
            duration = finite(note.get("duration_sec"), "note.duration_sec")
            if duration <= 0 or time + duration > end - start + 1e-5:
                raise ValueError("note must have positive duration and fit inside the recording")
            if "velocity" in note and finite(note["velocity"], "velocity") > 1:
                raise ValueError("velocity must be within 0-1")
    if event["source"] == "rhythm":
        from jammate.rhythm import RhythmContext
        try:
            rhythm = RhythmContext(**payload["rhythm"])
        except (KeyError, TypeError, OverflowError, ValueError) as exc:
            raise ValueError(f"invalid rhythm payload: {exc}") from exc
        if "onsets_sec" in payload:
            onsets = payload["onsets_sec"]
            if not isinstance(onsets, list):
                raise ValueError("onsets_sec must be a list")
            for onset in onsets:
                if finite(onset, "onsets_sec") > end - start + 1e-5:
                    raise ValueError("onsets_sec must fit inside the recording")
            if payload.get("quantized") is True:
                expected = [rhythm.beat_origin_s + b * 60 / rhythm.tempo_bpm for b in rhythm.onset_beats]
                if len(onsets) != len(expected) or any(abs(a-b) > 1e-5 for a,b in zip(onsets,expected)):
                    raise ValueError("quantized onsets_sec must match rhythm beat positions")
    if event["source"] == "speech" and (not isinstance(payload.get("text"), str) or not payload["text"].strip()):
        raise ValueError("speech payload requires nonempty text")
    # Also reject NaN/infinity hidden in extension fields.
    try:
        json.dumps(event, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("InputEvent must contain finite JSON data") from exc
    return event


def make_event(source, payload, duration_sec, *, session_id, started_at=0, confidence=None, event_id=None):
    duration_sec = finite(duration_sec, "duration_sec")
    started_at = finite(started_at, "started_at")
    pairs = {"humming": "musical_phrase", "rhythm": "rhythm_seed", "speech": "speech"}
    if not isinstance(source, str) or source not in pairs:
        raise ValueError("unsupported source")
    return validate_event(dict(event_id=event_id or f"evt_{uuid4().hex}", session_id=session_id,
        source=source, event_type=pairs[source],
        started_at=started_at, ended_at=started_at + duration_sec, payload=payload, confidence=confidence))


def from_melody_document(document, *, session_id, started_at=0):
    """Adapt existing Hum Lab JSON without changing its note detector."""
    notes = document["notes"] if "notes" in document else document["results"][document.get("selectedResult", "cleaned")]["notes"]
    payload = {"timebase": "seconds_from_recording_start", "tempo_bpm": None,
        "notes": [{"pitch_midi": n["pitchMidi"], "start_sec": n["startSec"],
                   "duration_sec": n["durationSec"], "velocity": n.get("velocity01", 90/127)} for n in notes],
        "status": "ready" if notes else "no_notes", "audio": document["source"],
        "analysis": {"backend": "basic-pitch", "variant": document.get("selectedResult", "cleaned"),
                     "confidence_meaning": "uncalibrated; model activations are not probabilities"}}
    return make_event("humming", payload, document["source"]["durationSec"],
                      session_id=session_id, started_at=started_at)


def perceive_rhythm(audio, sr, *, session_id, started_at=0, tempo_hint=None, count_in=4, beat_origin_s=None):
    from jammate.analysis.rhythm import analyze_rhythm_audio
    rhythm = analyze_rhythm_audio(audio, sr, tempo_hint, count_in, beat_origin_s)
    payload = {"timebase": "seconds_from_recording_start", "rhythm": rhythm.to_dict(),
        "onsets_sec": [rhythm.beat_origin_s + beat * 60/rhythm.tempo_bpm for beat in rhythm.onset_beats],
        "status": "ready" if rhythm.onset_beats else "no_onsets", "quantized": True,
        "analysis": {"backend": "upstream-librosa", "confidence_meaning": "grid-fit heuristic, not calibrated accuracy"}}
    return make_event("rhythm", payload, len(audio)/sr, session_id=session_id,
                      started_at=started_at, confidence=rhythm.confidence)


def write_event(event, path):
    validate_event(event)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(event, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def to_legacy_rhythm(event):
    """Temporary compatibility adapter; does not decide or mutate JamState."""
    from jammate.director.policy import UserRhythm
    from jammate.rhythm import RhythmContext
    validate_event(event)
    if event["source"] != "rhythm":
        raise ValueError("expected rhythm InputEvent")
    return UserRhythm(RhythmContext(**event["payload"]["rhythm"]))
