"""Renderer-independent decisions. No MIDI programs, prompts or generated notes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator

from .input_event import _json_values, read_json


class PlanError(ValueError):
    pass


@lru_cache(maxsize=1)
def _validator():
    schema = json.loads(files("jammate.contracts").joinpath("band_plan.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@dataclass(frozen=True, init=False)
class BandPlan:
    _json: str

    def __init__(self, data: dict):
        _json_values(data)
        data = json.loads(json.dumps(data, allow_nan=False))
        error = next(_validator().iter_errors(data), None)
        if error:
            raise PlanError(f"{'.'.join(map(str, error.absolute_path))}: {error.message}")
        for action in data["actions"]:
            params = action["parameters"]
            allowed = {"PRESERVE": set(), "ECHO_MOTIF": {"motif_id"}, "VARY_MOTIF": {"motif_id"},
                       "INTRODUCE_MOTIF": {"motif_id"}, "BUILD": {"duration_bars"}, "SIMPLIFY": {"duration_bars"},
                       "ADJUST_FEEL": {"rhythm_event_id", "level_delta", "brightness"},
                       "ADD_LAYER": set(), "REMOVE_LAYER": set(), "FILL": set()}
            if not set(params) <= allowed[action["action"]]:
                raise PlanError("parameters do not belong to this action")
            if params.get("duration_bars", 1) > data["valid_for_bars"]:
                raise PlanError("action duration exceeds plan lifetime")
            if action["action"] in {"ECHO_MOTIF", "VARY_MOTIF", "INTRODUCE_MOTIF"} and "motif_id" not in params:
                raise PlanError("motif action requires parameters.motif_id")
        if data["strategy"] == "INITIATE" and not any(a["action"] == "INTRODUCE_MOTIF" for a in data["actions"]):
            raise PlanError("INITIATE needs an INTRODUCE_MOTIF action")
        motifs = [a for a in data["actions"] if a["action"] in {"ECHO_MOTIF", "VARY_MOTIF", "INTRODUCE_MOTIF"}]
        if len(motifs) > 1 or any(a["scope"] not in {"lead", "motif"} for a in motifs):
            raise PlanError("baseline supports one lead/motif response per plan")
        if data["transport"].get("playing") is False and data["actions"]:
            raise PlanError("stop control cannot also request musical actions")
        object.__setattr__(self, "_json", json.dumps(data, ensure_ascii=False, sort_keys=True))

    def to_dict(self):
        return json.loads(self._json)

    @classmethod
    def from_dict(cls, data):
        return cls(data)

    @classmethod
    def load(cls, path):
        return cls(read_json(path))

    @property
    def plan_id(self):
        return self.to_dict()["plan_id"]

    @property
    def based_on_state_version(self):
        return self.to_dict()["based_on_state_version"]

    @property
    def actions(self):
        return self.to_dict()["actions"]

    def write(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
