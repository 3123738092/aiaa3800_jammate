"""Playable instruments that can join an existing Jam."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class AddedInstrument:
    label: str
    aliases: tuple[str, ...]
    midi_program: int
    midi_channel: int
    role: str


# General MIDI programs and channels are zero-based.
ADDED_INSTRUMENTS = {
    "guitar": AddedInstrument("木吉他", ("吉他", "acoustic guitar"), 25, 5,
                             "warm offbeat strummed or plucked accompaniment"),
    "flute": AddedInstrument("长笛", (), 73, 6,
                            "light, airy upper-register replies between phrases"),
    "strings": AddedInstrument("弦乐", ("弦乐组", "弦乐铺底", "string section"), 48, 7,
                              "soft sustained harmony and a fuller background"),
    "harp": AddedInstrument("竖琴", ("harp",), 46, 8,
                           "bright plucked flourishes and delicate broken chords"),
}

INSTRUMENT_ALIASES = {alias: name for name, instrument in ADDED_INSTRUMENTS.items()
                      for alias in (name, instrument.label, *instrument.aliases)}

GM_SCOPE_PATTERN = r"gm_(?:0|[1-9][0-9]?|1[01][0-9]|12[0-7])"
MAX_JAM_LAYERS = 16  # Fifteen pitched MIDI channels and one drum channel.


def gm_program(name: str) -> int | None:
    return int(name[3:]) if isinstance(name, str) and re.fullmatch(GM_SCOPE_PATTERN, name) else None


def added_program(name: str) -> int | None:
    instrument = ADDED_INSTRUMENTS.get(name)
    return instrument.midi_program if instrument else gm_program(name)
