"""JSON Schema for the versioned wire view of the existing JamState."""
from __future__ import annotations

import json
from functools import lru_cache
from jsonschema import Draft202012Validator
from jammate.interaction import CORE_LAYER_NAMES, LAYER_NAMES
from jammate.instruments import GM_SCOPE_PATTERN, MAX_JAM_LAYERS
from jammate.music import CHORD_INTERVALS, MAX_TEMPO_BPM, MIN_TEMPO_BPM
from jammate.texture import TEXTURE_CHOICES
from .input_event import _json_values, input_event_schema


def state_schema():
    def obj(properties):
        return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}
    text = {"type": "string", "minLength": 1}
    integer = {"type": "integer", "minimum": 0}
    score = {"type": "number", "minimum": 0, "maximum": 1}
    boolean = {"type": "boolean"}
    notes = {"type": "array", "items": obj({
        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
        "start": {"type": "number", "minimum": 0}, "duration": {"type": "number", "exclusiveMinimum": 0},
        "velocity": {"type": "integer", "minimum": 0, "maximum": 127}, "instrument": text,
    })}
    rhythm = input_event_schema()["$defs"]["rhythmPayload"]["properties"]["rhythm"]
    # Localize the only reference used by the reused input rhythm shape.
    rhythm = json.loads(json.dumps(rhythm).replace('"#/$defs/score"', '"#/$defs/unit"'))
    result = obj({
        "schema_version": {"const": "1.0.0"}, "session_id": text, "state_version": integer,
        "transport": obj({"tempo_bpm": {"type": "number", "minimum": MIN_TEMPO_BPM, "maximum": MAX_TEMPO_BPM}, "playing": boolean,
                          "bar": integer, "beat": {"type": "number", "minimum": 0, "exclusiveMaximum": 4}, "next_bar": integer}),
        "harmony": obj({"key": {"anyOf": [{"type": "null"}, obj({"tonic": {"type": "integer", "minimum": 0, "maximum": 11}, "mode": {"enum": ["major", "minor"]}})]}, "progression_variant": integer}),
        "form": obj({"section": text, "bars_elapsed": integer}),
        "feel": obj({"energy": {"type": "integer", "minimum": 0, "maximum": 3}, "density": score, "brightness": score, "tension": score, "tags": {"type": "array", "items": text}}),
        "motifs": obj({"current_id": {"type": ["string", "null"]}, "last_user": notes, "last_ai": notes,
                       "items": {"type": "array", "items": obj({"motif_id": text, "owner": {"enum": ["user", "ai"]}, "source_event_ids": {"type": "array", "uniqueItems": True, "items": text}, "notes": notes})}}),
        "arrangement": obj({"layers": {"type": "array", "minItems": len(CORE_LAYER_NAMES), "maxItems": MAX_JAM_LAYERS,
                                       "items": obj({"name": {"anyOf": [{"enum": list(LAYER_NAMES)},
                                                                      {"type": "string", "pattern": f"^{GM_SCOPE_PATTERN}$"}]},
                                                     "active": boolean,
                                                     "level": score, "density": score, "role": text})}}),
        "interaction": obj({"strategy": {"enum": ["LISTEN", "FOLLOW", "RESPOND", "INITIATE"]}, "initiative": score,
                            "current_lead": {"enum": ["user", "ai", None]}, "user_active": boolean, "awaiting_user": boolean, "last_feedback": {"type": ["string", "null"]}}),
        "rhythm": {"anyOf": [{"type": "null"}, rhythm]}, "rhythm_mode": {"enum": ["follow", "vary", "simplify"]},
        "rhythm_sources": {"type": "array", "items": obj({"event_id": text, "context": rhythm})},
    })
    result["properties"]["motifs"]["properties"]["accompaniment"] = obj({"motif_id": text, "start_bar": integer})
    result["properties"]["arrangement"]["properties"]["texture"] = obj(
        {name: {"enum": list(values)} for name, values in TEXTURE_CHOICES.items()}
    )
    result["properties"]["arrangement"]["properties"]["style"] = {"type": "string", "minLength": 1, "maxLength": 240}
    result["properties"]["interaction"]["properties"]["allow_initiative"] = boolean
    result["properties"]["harmony"]["properties"]["auto_harmony"] = boolean
    result["properties"]["harmony"]["properties"]["progression"] = obj({
        "start_bar": integer,
        "chords": {"type": "array", "minItems": 2, "maxItems": 8, "items": obj({
            "offset": {"type": "integer", "minimum": 0, "maximum": 11},
            "quality": {"enum": list(CHORD_INTERVALS)},
        })},
    })
    result.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "JamMate JamState 1.0.0", "$defs": {"unit": score}})
    return result


@lru_cache(maxsize=1)
def _validator():
    schema = state_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_state(data):
    _json_values(data)
    error = next(_validator().iter_errors(json.loads(json.dumps(data, allow_nan=False))), None)
    if error:
        raise ValueError(f"JamState {'.'.join(map(str, error.absolute_path))}: {error.message}")
