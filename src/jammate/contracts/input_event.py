"""Validated, immutable InputEvent snapshots and strict JSON file I/O.

Event times are session-relative capture times, not inference completion times.
They are both null when an imported file has no known session-clock mapping.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

VERSION = "1.0.0"
TIME_TOLERANCE_S = 1e-5  # JSON rounding only; not an audio synchronization budget.


class InputEventError(ValueError):
    """Invalid wire data; messages identify the offending field."""


def _json_values(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InputEventError(f"{path}: object keys must be strings")
            _json_values(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _json_values(item, f"{path}[{index}]")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InputEventError(f"{path}: NaN and Infinity are not allowed")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise InputEventError(f"{path}: expected a JSON value, got {type(value).__name__}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InputEventError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: str | Path) -> Any:
    """Reject duplicate fields instead of silently taking the last value."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InputEventError(f"invalid JSON: {exc}") from exc
    _json_values(data)
    return data


def input_event_schema() -> dict[str, Any]:
    """Return a fresh copy of the packaged, language-independent JSON Schema."""
    return json.loads(files("jammate.contracts").joinpath("input_event.schema.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = input_event_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _notes_in_bounds(notes: list[dict[str, Any]], duration: float, path: str) -> None:
    ids = [note["id"] for note in notes]
    if len(ids) != len(set(ids)):
        raise InputEventError(f"{path}: duplicate note id")
    positions = [(note["startSec"], note["pitchMidi"]) for note in notes]
    if positions != sorted(positions):
        raise InputEventError(f"{path}: notes must be ordered by startSec, then pitchMidi")
    for index, note in enumerate(notes):
        if note["startSec"] + note["durationSec"] > duration + TIME_TOLERANCE_S:
            raise InputEventError(f"{path}[{index}]: note ends outside source audio")


def _cross_validate(data: dict[str, Any]) -> None:
    start, end = data["started_at"], data["ended_at"]
    if (start is None) != (end is None):
        raise InputEventError("started_at/ended_at: provide both session times or both null")
    if start is not None and end < start:
        raise InputEventError("ended_at: must be >= started_at")
    if data["confidence"] is not None and not data.get("provenance", {}).get("confidence_method"):
        raise InputEventError("provenance.confidence_method: required for non-null event confidence")
    payload = data["payload"]
    if data["event_type"] == "session_control":
        if start != end:
            raise InputEventError("session_control timestamps describe one instant")
        return
    if data["event_type"] == "speech":
        return
    if data["event_type"] == "rhythm_seed":
        rhythm = payload["rhythm"]
        beats, accents = rhythm["onset_beats"], rhythm["accents"]
        if len(beats) != len(accents):
            raise InputEventError("payload.rhythm: onset_beats and accents lengths differ")
        if any(a >= b for a, b in zip(beats, beats[1:])):
            raise InputEventError("payload.rhythm.onset_beats: must be strictly increasing")
        expected = "ready" if beats else "no_onsets"
        if payload["status"] != expected:
            raise InputEventError(f"payload.status: expected {expected}")
        duration = payload["audio_duration_s"]
        bar_end = rhythm["beat_origin_s"] + rhythm["beats_per_bar"] * 60 / rhythm["tempo_bpm"]
        if bar_end > duration + TIME_TOLERANCE_S:
            raise InputEventError("payload.audio_duration_s: must contain the complete rhythm bar")
    else:
        melody = payload["melody"]
        if melody["id"] != melody["source"]["id"]:
            raise InputEventError("payload.melody: id must match source.id")
        duration = melody["source"]["durationSec"]
        raw = melody["results"]["raw"]["notes"]
        cleaned = melody["results"]["cleaned"]["notes"]
        if melody["notes"] != melody["results"][melody["selectedResult"]]["notes"]:
            raise InputEventError("payload.melody.notes: does not match selectedResult")
        expected = "ready" if raw else "no_notes"
        if melody["status"] != expected:
            raise InputEventError(f"payload.melody.status: expected {expected}")
        if bool(raw) != bool(cleaned):
            raise InputEventError("payload.melody.results: cleanup cannot invent or erase all notes")
        raw_ids = {note["id"] for note in raw}
        for label, notes in (("raw", raw), ("cleaned", cleaned)):
            path = f"payload.melody.results.{label}.notes"
            _notes_in_bounds(notes, duration, path)
            for note in notes:
                if not set(note["sourceNoteIds"]).issubset(raw_ids):
                    raise InputEventError(f"{path}: sourceNoteIds must reference raw notes")
    if start is not None and abs((end - start) - duration) > TIME_TOLERANCE_S:
        raise InputEventError("started_at/ended_at: interval must match original audio duration")


@dataclass(frozen=True, init=False)
class InputEvent:
    """A validated snapshot. Returned dicts are copies, so nested edits are safe."""

    _json: str

    def __init__(self, document: dict[str, Any]) -> None:
        _json_values(document)
        # Also converts internal tuples to wire arrays without changing text.
        data = json.loads(json.dumps(document, ensure_ascii=False, allow_nan=False))
        error = next(_validator().iter_errors(data), None)
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            raise InputEventError(f"{path}: {error.message}")
        _cross_validate(data)
        object.__setattr__(self, "_json", json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InputEvent:
        return cls(data)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._json)

    @property
    def event_id(self) -> str:
        return self.to_dict()["event_id"]

    @property
    def session_id(self) -> str:
        return self.to_dict()["session_id"]

    @property
    def schema_version(self) -> str:
        return self.to_dict()["schema_version"]

    @property
    def source(self) -> str:
        return self.to_dict()["source"]

    @property
    def event_type(self) -> str:
        return self.to_dict()["event_type"]

    @property
    def started_at(self) -> float | None:
        return self.to_dict()["started_at"]

    @property
    def ended_at(self) -> float | None:
        return self.to_dict()["ended_at"]

    @property
    def confidence(self) -> float | None:
        return self.to_dict()["confidence"]

    @property
    def provenance(self) -> dict[str, Any]:
        return self.to_dict().get("provenance", {})

    @property
    def payload(self) -> dict[str, Any]:
        return self.to_dict()["payload"]

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return path


def load_input_event(path: str | Path) -> InputEvent:
    return InputEvent.from_dict(read_json(path))
