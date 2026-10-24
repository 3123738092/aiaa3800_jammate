from __future__ import annotations

from dataclasses import dataclass

from .models import Harmony, MusicIntent, Parts, Semantic, Tempo


@dataclass(frozen=True)
class InstrumentProfile:
    name: str
    programs: dict[int, int]
    description: str


INSTRUMENT_PROFILES = {
    "acoustic": InstrumentProfile("acoustic", {1: 0, 2: 33}, "Acoustic Grand Piano + Fingered Bass"),
    "electric": InstrumentProfile("electric", {1: 4, 2: 33}, "Electric Piano 1 + Fingered Bass"),
    "electric_performance": InstrumentProfile("electric_performance", {1: 4, 2: 33, 3: 7}, "Electric Piano 1 + Fingered Bass + Clavinet ornament"),
    "synth": InstrumentProfile("synth", {1: 88, 2: 38}, "Pad 1 (new age) + Synth Bass 1"),
}

STYLE_PRESETS: dict[str, MusicIntent] = {
    "warm_funk": MusicIntent(prompt="tight live-band funk groove", semantic=Semantic(("funk",), ("warm", "groovy"), .7, .55, .55)),
    "indie_rock": MusicIntent(prompt="indie rock band, driving drums, melodic bass", semantic=Semantic(("indie rock",), ("energetic", "raw"), .85, .75, .65)),
    "lofi": MusicIntent(prompt="lo-fi hip hop, dusty drums, mellow keys", semantic=Semantic(("lo-fi hip hop",), ("mellow", "dusty"), .4, .4, .3)),
    "jazz_fusion": MusicIntent(prompt="jazz fusion, syncopated drums, electric bass", harmony=Harmony("D", "major", ("Dmaj7", "Bm7", "Em7", "A7")), semantic=Semantic(("jazz fusion",), ("curious", "fluid"), .75, .8, .8)),
    "ambient": MusicIntent(prompt="ambient electronic texture, spacious evolving harmony", tempo=Tempo(80), semantic=Semantic(("ambient", "electronic"), ("spacious", "calm"), .3, .25, .8), parts=Parts(.35, .3, .8)),
}


def get_style(name: str) -> MusicIntent:
    try:
        return STYLE_PRESETS[name]
    except KeyError as exc:
        raise ValueError(f"unknown style {name}; choose from {', '.join(STYLE_PRESETS)}") from exc


def get_instrument(name: str) -> InstrumentProfile:
    try:
        return INSTRUMENT_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown instrument profile {name}; choose from {', '.join(INSTRUMENT_PROFILES)}") from exc
