from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Tempo:
    bpm: float = 100.0


@dataclass(frozen=True)
class Harmony:
    key: str = "C"
    mode: str = "major"
    chords: tuple[str, ...] = ("Cmaj7", "Am7", "Dm7", "G7")


@dataclass(frozen=True)
class Semantic:
    style: tuple[str, ...] = ("funk",)
    mood: tuple[str, ...] = ("warm", "groovy")
    energy: float = 0.7
    density: float = 0.5
    brightness: float = 0.6


@dataclass(frozen=True)
class Parts:
    drums: float = 0.8
    bass: float = 0.9
    harmony: float = 0.5


@dataclass(frozen=True)
class MusicIntent:
    tempo: Tempo = field(default_factory=Tempo)
    harmony: Harmony = field(default_factory=Harmony)
    semantic: Semantic = field(default_factory=Semantic)
    parts: Parts = field(default_factory=Parts)
    prompt: str = "tight live-band groove"

    def __post_init__(self) -> None:
        if not 20 <= self.tempo.bpm <= 300:
            raise ValueError("tempo.bpm must be between 20 and 300")
        for name, value in (
            ("energy", self.semantic.energy),
            ("density", self.semantic.density),
            ("brightness", self.semantic.brightness),
            ("parts.drums", self.parts.drums),
            ("parts.bass", self.parts.bass),
            ("parts.harmony", self.parts.harmony),
        ):
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("style", "mood", "chords"):
            section = "semantic" if key in ("style", "mood") else "harmony"
            data[section][key] = list(data[section][key])
        return data

    def patch(self, **changes: Any) -> "MusicIntent":
        """Return a validated intent with nested section updates."""
        current = self.to_dict()
        for section, values in changes.items():
            if section not in current or not isinstance(values, dict):
                raise ValueError(f"unsupported intent patch section: {section}")
            current[section].update(values)
        if "chords" in current["harmony"]:
            current["harmony"]["chords"] = tuple(current["harmony"]["chords"])
        for key in ("style", "mood"):
            current["semantic"][key] = tuple(current["semantic"][key])
        return MusicIntent(
            tempo=Tempo(**current["tempo"]),
            harmony=Harmony(**current["harmony"]),
            semantic=Semantic(**current["semantic"]),
            parts=Parts(**current["parts"]),
            prompt=current["prompt"],
        )
