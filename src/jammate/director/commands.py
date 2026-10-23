"""Spoken/typed band directions -> intents (English and Chinese keywords).

Rule-based on purpose: it is instant, free and predictable, so the band
reacts within the current bar. An LLM interpreter can sit in front of this
for free-form requests, but must map onto the same `Intent` set.

Known limitation: negation is not understood ("don't stop" reads as STOP).
"""

from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    FASTER = "faster"
    SLOWER = "slower"
    MORE_ENERGY = "more_energy"
    LESS_ENERGY = "less_energy"
    KEEP = "keep"
    VARY = "vary"
    BREAK = "break"
    BUILD = "build"
    NEW_IDEA = "new_idea"
    STOP = "stop"
    RHYTHM_FOLLOW = "rhythm_follow"
    RHYTHM_VARY = "rhythm_vary"
    RHYTHM_SIMPLIFY = "rhythm_simplify"


_PHRASES: tuple[tuple[str, Intent], ...] = (
    ("follow rhythm", Intent.RHYTHM_FOLLOW), ("跟随节奏", Intent.RHYTHM_FOLLOW),
    ("vary rhythm", Intent.RHYTHM_VARY), ("变化节奏", Intent.RHYTHM_VARY),
    ("simplify", Intent.RHYTHM_SIMPLIFY), ("简化", Intent.RHYTHM_SIMPLIFY),
    ("speed up", Intent.FASTER), ("faster", Intent.FASTER), ("pick it up", Intent.FASTER),
    ("快一点", Intent.FASTER), ("快点", Intent.FASTER), ("加速", Intent.FASTER),
    ("slow down", Intent.SLOWER), ("slower", Intent.SLOWER),
    ("慢一点", Intent.SLOWER), ("慢点", Intent.SLOWER), ("放慢", Intent.SLOWER),
    ("more powerful", Intent.MORE_ENERGY), ("more energy", Intent.MORE_ENERGY),
    ("heavier", Intent.MORE_ENERGY), ("louder", Intent.MORE_ENERGY), ("harder", Intent.MORE_ENERGY),
    ("更有力", Intent.MORE_ENERGY), ("重一点", Intent.MORE_ENERGY), ("燃", Intent.MORE_ENERGY),
    ("大声", Intent.MORE_ENERGY),
    ("softer", Intent.LESS_ENERGY), ("quieter", Intent.LESS_ENERGY), ("calm down", Intent.LESS_ENERGY),
    ("gentle", Intent.LESS_ENERGY), ("轻一点", Intent.LESS_ENERGY), ("安静", Intent.LESS_ENERGY),
    ("柔和", Intent.LESS_ENERGY), ("小声", Intent.LESS_ENERGY),
    ("keep", Intent.KEEP), ("nice", Intent.KEEP), ("love it", Intent.KEEP),
    ("保持", Intent.KEEP), ("不错", Intent.KEEP), ("好听", Intent.KEEP), ("就这样", Intent.KEEP),
    ("change", Intent.VARY), ("something different", Intent.VARY), ("switch it up", Intent.VARY),
    ("换一个", Intent.VARY), ("换个", Intent.VARY), ("变一下", Intent.VARY),
    ("break", Intent.BREAK), ("drop", Intent.BREAK), ("停一下", Intent.BREAK), ("留白", Intent.BREAK),
    ("build", Intent.BUILD), ("推上去", Intent.BUILD), ("渐强", Intent.BUILD),
    ("your turn", Intent.NEW_IDEA), ("new idea", Intent.NEW_IDEA), ("give me", Intent.NEW_IDEA),
    ("你来", Intent.NEW_IDEA), ("来一段", Intent.NEW_IDEA), ("新的", Intent.NEW_IDEA),
    ("stop", Intent.STOP), ("that's it", Intent.STOP),
    ("结束", Intent.STOP), ("停止", Intent.STOP), ("停", Intent.STOP),
)
# Longest phrases first, so "停一下" (break) wins over "停" (stop).
_ORDERED = tuple(sorted(_PHRASES, key=lambda pair: len(pair[0]), reverse=True))


def _pattern(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase)
    if phrase.isascii():
        return re.compile(rf"\b{escaped}\b")
    return re.compile(escaped)


_PATTERNS = tuple((_pattern(phrase), intent) for phrase, intent in _ORDERED)


def parse_command(text: str) -> tuple[Intent, ...]:
    """Intents in the order they were said, without repeats."""
    working = text.lower()
    found: list[tuple[int, Intent]] = []
    for pattern, intent in _PATTERNS:
        for match in pattern.finditer(working):
            found.append((match.start(), intent))
        working = pattern.sub(lambda m: " " * len(m.group(0)), working)
    ordered: list[Intent] = []
    for _, intent in sorted(found, key=lambda pair: pair[0]):
        if intent not in ordered:
            ordered.append(intent)
    return tuple(ordered)
