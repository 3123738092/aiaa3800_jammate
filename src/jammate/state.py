"""Shared jam state: the single record the band director reads and replaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
import json
from enum import Enum

from jammate.music import MAX_TEMPO_BPM, MIN_TEMPO_BPM, ChordStep, Key, Note
from jammate.texture import ArrangementTexture
from jammate.rhythm import RhythmContext, RhythmMode
from jammate.interaction import (
    CORE_LAYER_NAMES, AccompanimentMotif, Interaction, Layer, Motif, RhythmSource, TransportPosition, unit,
)
from jammate.instruments import MAX_JAM_LAYERS

MAX_ENERGY = 3


class Action(str, Enum):
    FOLLOW = "follow"  # answer the user's phrase
    VARY = "vary"  # change harmony / re-spin the last idea
    BUILD = "build"  # raise intensity
    BREAK = "break"  # drop to a sparse texture
    INITIATE = "initiate"  # AI throws a new motif to the user
    ADJUST = "adjust"  # tempo/energy tweak without new material
    HOLD = "hold"  # keep the groove as is
    STOP = "stop"


@dataclass(frozen=True)
class JamState:
    tempo_bpm: float = 96.0
    key: Key | None = Key(0, "major")
    energy: int = 1
    progression_variant: int = 0
    last_user_motif: tuple[Note, ...] = ()
    last_ai_motif: tuple[Note, ...] = ()
    playing: bool = True
    rhythm: RhythmContext | None = None
    rhythm_mode: RhythmMode = RhythmMode.FOLLOW
    session_id: str = "default"
    state_version: int = 0
    position: TransportPosition = TransportPosition()
    section: str = "groove"
    section_bars: int = 0
    density: float = 1.0
    brightness: float = 0.5
    tension: float = 0.0
    tags: tuple[str, ...] = ()
    layers: tuple[Layer, ...] = (Layer("drums", role="timekeeper"), Layer("bass"), Layer("keys"), Layer("lead", role="response"))
    motifs: tuple[Motif, ...] = ()
    current_motif_id: str | None = None
    rhythm_sources: tuple[RhythmSource, ...] = ()
    interaction: Interaction = Interaction()
    texture: ArrangementTexture = ArrangementTexture()
    chord_cycle: tuple[ChordStep, ...] = ()
    progression_start_bar: int = 0
    auto_harmony: bool = True
    accompaniment_motif: AccompanimentMotif | None = None
    style: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rhythm_mode", RhythmMode(self.rhythm_mode))
        for name in ("last_user_motif", "last_ai_motif", "tags", "layers", "motifs", "rhythm_sources", "chord_cycle"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not isinstance(self.session_id, str) or not self.session_id.strip() or type(self.state_version) is not int or self.state_version < 0:
            raise ValueError("invalid session_id/state_version")
        if isinstance(self.tempo_bpm, bool) or not math.isfinite(self.tempo_bpm):
            raise ValueError("tempo must be finite")
        if not MIN_TEMPO_BPM <= self.tempo_bpm <= MAX_TEMPO_BPM:
            raise ValueError(
                f"tempo must be within {MIN_TEMPO_BPM}-{MAX_TEMPO_BPM} BPM, got {self.tempo_bpm}"
            )
        if type(self.energy) is not int or not 0 <= self.energy <= MAX_ENERGY:
            raise ValueError(f"energy must be within 0-{MAX_ENERGY}, got {self.energy}")
        if type(self.playing) is not bool or type(self.section_bars) is not int or self.section_bars < 0:
            raise ValueError("invalid playing/section_bars")
        for name in ("density", "brightness", "tension"):
            unit(getattr(self, name), name)
        for entries, key in ((self.layers, "name"), (self.motifs, "motif_id"), (self.rhythm_sources, "event_id")):
            ids = [getattr(item, key) for item in entries]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {key}")
        if not set(CORE_LAYER_NAMES).issubset(layer.name for layer in self.layers):
            raise ValueError("state requires drums, bass, keys and lead layers (active or muted)")
        if len(self.layers) > MAX_JAM_LAYERS:
            raise ValueError("arrangement exceeds the available MIDI channels")
        if self.chord_cycle and (not 2 <= len(self.chord_cycle) <= 8
                                 or any(not isinstance(step, ChordStep) for step in self.chord_cycle)):
            raise ValueError("custom progression requires 2–8 chord steps")
        if type(self.progression_start_bar) is not int or self.progression_start_bar < 0:
            raise ValueError("progression_start_bar must be a nonnegative integer")
        if type(self.auto_harmony) is not bool:
            raise ValueError("auto_harmony must be a boolean")
        if self.style is not None and (not isinstance(self.style, str) or not 0 < len(self.style.strip()) <= 240):
            raise ValueError("style must be a short musical description")
        if self.current_motif_id is not None and self.current_motif_id not in {m.motif_id for m in self.motifs}:
            raise ValueError("current_motif_id must reference a registered motif")
        if self.accompaniment_motif is not None and self.accompaniment_motif.motif_id not in {
                motif.motif_id for motif in self.motifs if motif.owner == "user"}:
            raise ValueError("accompaniment must reference a registered user motif")

    def with_tempo(self, bpm: float) -> JamState:
        return replace(self, tempo_bpm=min(MAX_TEMPO_BPM, max(MIN_TEMPO_BPM, bpm)))

    def with_energy(self, level: int) -> JamState:
        return replace(self, energy=min(MAX_ENERGY, max(0, level)))

    def with_updates(self, **changes: object) -> JamState:
        return replace(self, **changes)

    def layer(self, name: str) -> Layer:
        return next(layer for layer in self.layers if layer.name == name)

    def motif(self, motif_id: str) -> Motif:
        return next(motif for motif in self.motifs if motif.motif_id == motif_id)

    def to_dict(self) -> dict:
        """SPEC wire view of the same state, not a second writable state."""
        data = {
            "schema_version": "1.0.0", "session_id": self.session_id, "state_version": self.state_version,
            "transport": {**asdict(self.position), "tempo_bpm": self.tempo_bpm, "playing": self.playing},
            "harmony": {"key": asdict(self.key) if self.key else None, "progression_variant": self.progression_variant},
            "form": {"section": self.section, "bars_elapsed": self.section_bars},
            "feel": {"energy": self.energy, "density": self.density, "brightness": self.brightness, "tension": self.tension, "tags": list(self.tags)},
            "motifs": {"current_id": self.current_motif_id,
                       "items": [{"motif_id": m.motif_id, "owner": m.owner, "source_event_ids": list(m.source_event_ids), "notes": [asdict(n) for n in m.notes]} for m in self.motifs],
                       "last_user": [asdict(n) for n in self.last_user_motif], "last_ai": [asdict(n) for n in self.last_ai_motif]},
            "arrangement": {"layers": [asdict(layer) for layer in self.layers]},
            "interaction": {**asdict(self.interaction), "strategy": self.interaction.strategy.value},
            "rhythm": self.rhythm.to_dict() if self.rhythm else None, "rhythm_mode": self.rhythm_mode.value,
            "rhythm_sources": [{"event_id": r.event_id, "context": r.context.to_dict()} for r in self.rhythm_sources],
        }
        if self.accompaniment_motif is not None:
            data["motifs"]["accompaniment"] = asdict(self.accompaniment_motif)
        if self.texture != ArrangementTexture():
            data["arrangement"]["texture"] = asdict(self.texture)
        if self.style is not None:
            data["arrangement"]["style"] = self.style
        if self.interaction.allow_initiative:
            data["interaction"].pop("allow_initiative")
        if self.chord_cycle:
            data["harmony"]["progression"] = {"start_bar": self.progression_start_bar,
                                             "chords": [asdict(step) for step in self.chord_cycle]}
        if not self.auto_harmony:
            data["harmony"]["auto_harmony"] = False
        return json.loads(json.dumps(data, allow_nan=False))

    @classmethod
    def from_dict(cls, data: dict) -> JamState:
        from jammate.contracts.state_codec import validate_state
        validate_state(data)
        transport, harmony, form, feel, motifs = (data[k] for k in ("transport", "harmony", "form", "feel", "motifs"))
        progression = harmony.get("progression", {})
        return cls(
            session_id=data["session_id"], state_version=data["state_version"],
            tempo_bpm=transport["tempo_bpm"], playing=transport["playing"],
            position=TransportPosition(**{k: transport[k] for k in ("bar", "beat", "next_bar")}),
            key=Key(**harmony["key"]) if harmony["key"] else None, progression_variant=harmony["progression_variant"],
            section=form["section"], section_bars=form["bars_elapsed"], **feel,
            last_user_motif=tuple(Note(**n) for n in motifs["last_user"]), last_ai_motif=tuple(Note(**n) for n in motifs["last_ai"]),
            motifs=tuple(Motif(m["motif_id"], m["owner"], tuple(m["source_event_ids"]), tuple(Note(**n) for n in m["notes"])) for m in motifs["items"]),
            current_motif_id=motifs["current_id"], layers=tuple(Layer(**layer) for layer in data["arrangement"]["layers"]),
            interaction=Interaction(**data["interaction"]), rhythm=RhythmContext(**data["rhythm"]) if data["rhythm"] else None,
            rhythm_mode=data["rhythm_mode"], rhythm_sources=tuple(RhythmSource(r["event_id"], RhythmContext(**r["context"])) for r in data["rhythm_sources"]),
            texture=ArrangementTexture(**data["arrangement"].get("texture", {})),
            style=data["arrangement"].get("style"),
            chord_cycle=tuple(ChordStep(**step) for step in progression.get("chords", ())),
            progression_start_bar=progression.get("start_bar", 0),
            auto_harmony=harmony.get("auto_harmony", True),
            accompaniment_motif=AccompanimentMotif(**motifs["accompaniment"]) if "accompaniment" in motifs else None,
        )
