"""Lossless input wrapping and explicit, opt-in legacy compatibility bridges."""

from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from jammate.rhythm import RhythmContext

from .input_event import VERSION, InputEvent, InputEventError


def _capture_end(started_at: float | None, duration: float) -> float | None:
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise InputEventError("audio duration must be a finite positive number")
    if started_at is None:
        return None
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)) or not math.isfinite(started_at) or started_at < 0:
        raise InputEventError("started_at must be nonnegative session seconds or null")
    return started_at + duration


def _event(*, session_id: str, event_id: str | None, source: str, event_type: str,
           started_at: float | None, ended_at: float | None, payload: dict[str, Any],
           provenance: dict[str, Any]) -> InputEvent:
    return InputEvent.from_dict({
        "schema_version": VERSION,
        "event_id": event_id if event_id is not None else f"evt_{uuid4().hex}",
        "session_id": session_id, "source": source, "event_type": event_type,
        "started_at": started_at, "ended_at": ended_at, "confidence": None,
        "payload": payload, "provenance": provenance,
    })


def _provenance(producer: str, audio_ref: str | None, analysis_ref: str | None) -> dict[str, Any]:
    return {key: value for key, value in {
        "producer": producer, "audio_ref": audio_ref, "analysis_ref": analysis_ref,
    }.items() if value is not None}


def from_text(text: str, *, session_id: str, event_id: str | None = None,
              source: str = "text", started_at: float | None = None,
              ended_at: float | None = None, language: str | None = None,
              alternatives: list[str] | None = None, audio_ref: str | None = None) -> InputEvent:
    """Wrap submitted text/final ASR without trimming, paraphrasing or Intent parsing.

    The SPEC's event_type='speech' denotes a language message; source='text'
    explicitly distinguishes a typed message from source='speech' (ASR).
    """
    if source not in {"text", "speech"}:
        raise InputEventError("source: language input must be text or speech")
    payload: dict[str, Any] = {"text": text}
    if language is not None:
        payload["language"] = language
    if alternatives is not None:
        payload["alternatives"] = alternatives
    return _event(session_id=session_id, event_id=event_id, source=source, event_type="speech",
                  started_at=started_at, ended_at=ended_at, payload=payload,
                  provenance=_provenance("text" if source == "text" else "asr", audio_ref, None))


def from_rhythm(context: RhythmContext, *, session_id: str, audio_duration_s: float,
                tempo_source: str, event_id: str | None = None, started_at: float | None = None,
                confidence_kind: str = "unspecified", audio_ref: str | None = None,
                analysis_ref: str | None = None) -> InputEvent:
    """Wrap a one-bar context without assigning its heuristic to event confidence.

    started_at maps second zero of the ORIGINAL input recording to the session.
    Do not subtract a count-in: context.beat_origin_s still includes that offset.
    """
    return _event(
        session_id=session_id, event_id=event_id, source="rhythm", event_type="rhythm_seed",
        started_at=started_at, ended_at=_capture_end(started_at, audio_duration_s),
        payload={"status": "ready" if context.onset_beats else "no_onsets",
                 "audio_duration_s": audio_duration_s, "tempo_source": tempo_source,
                 "confidence_kind": confidence_kind, "rhythm": context.to_dict()},
        provenance=_provenance("legacy_rhythm", audio_ref, analysis_ref),
    )


def from_humming(document: dict[str, Any], *, session_id: str, event_id: str | None = None,
                 started_at: float | None = None, audio_ref: str | None = None,
                 analysis_ref: str | None = None) -> InputEvent:
    """Preserve the complete exported Melody v1 document, including raw evidence.

    The supplied start maps ORIGINAL recording second zero, not the trimmed
    clip, first note, createdAt, or inference completion time. No tempo is guessed.
    """
    if not isinstance(document, dict) or not isinstance(document.get("source"), dict):
        raise InputEventError("melody.source: expected the exported Melody v1 document")
    duration = document["source"].get("durationSec")
    ended_at = _capture_end(started_at, duration)
    provenance = _provenance("melody_v1", audio_ref, analysis_ref)
    provenance.update(source_id=document.get("id"), source_schema_version=document.get("schemaVersion"))
    return _event(session_id=session_id, event_id=event_id, source="humming", event_type="musical_phrase",
                  started_at=started_at, ended_at=ended_at, payload={"melody": document},
                  provenance=provenance)


def to_legacy_rhythm(event: InputEvent):
    """Compatibility only: a session-grid measurement cannot reset session BPM."""
    from jammate.director.policy import UserRhythm

    if event.event_type != "rhythm_seed":
        raise InputEventError("expected a rhythm_seed event")
    payload = event.payload
    return UserRhythm(RhythmContext(**payload["rhythm"]), update_tempo=payload["tempo_source"] != "session")


def from_system(command: str, *, session_id: str, event_id: str | None = None,
                occurred_at: float | None = None, active: bool | None = None,
                bars: int | None = None) -> InputEvent:
    """InputEvent 1.1 lifecycle facts from Runtime/input activity detection."""
    payload = {"command": command}
    if active is not None:
        payload["active"] = active
    if bars is not None:
        payload["bars"] = bars
    return InputEvent({"schema_version": "1.1.0", "session_id": session_id,
                       "event_id": event_id if event_id is not None else f"evt_{uuid4().hex}",
                       "source": "system", "event_type": "session_control", "started_at": occurred_at,
                       "ended_at": occurred_at, "confidence": None, "payload": payload,
                       "provenance": {"producer": "runtime"}})


def to_legacy_phrase(event: InputEvent, *, tempo_bpm: float, beat_origin_s: float = 0.0):
    """Seconds -> local beats, preserving rests, overlaps and selected pitches.

    Callers explicitly supply a constant session tempo and beat zero in the
    original recording. This is not a tempo-map adapter or quantization step.
    Legacy decide() later normalizes motifs; it must not overwrite this event.
    """
    from jammate.director.policy import UserPhrase
    from jammate.music import Note

    if event.event_type != "musical_phrase":
        raise InputEventError("expected a musical_phrase event")
    if isinstance(tempo_bpm, bool) or not isinstance(tempo_bpm, (int, float)) or not math.isfinite(tempo_bpm) or not 60 <= tempo_bpm <= 180:
        raise InputEventError("tempo_bpm: provide an explicit session tempo within 60-180")
    if isinstance(beat_origin_s, bool) or not isinstance(beat_origin_s, (int, float)) or not math.isfinite(beat_origin_s) or beat_origin_s < 0:
        raise InputEventError("beat_origin_s: expected a finite nonnegative recording time")
    notes = []
    for note in event.payload["melody"]["notes"]:
        start = (note["startSec"] - beat_origin_s) * tempo_bpm / 60
        if start < 0:
            raise InputEventError("beat_origin_s: would create a negative note start; no notes were dropped")
        notes.append(Note(int(note["pitchMidi"]), start, note["durationSec"] * tempo_bpm / 60,
                          max(1, round(note["velocity01"] * 127)), "user"))
    return UserPhrase(tuple(notes))
