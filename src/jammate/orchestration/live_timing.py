"""Timing intent for a live turn, independent of text classification."""
from dataclasses import dataclass
import re


_NEXT = re.compile(r"(?:下(?:一)?个?小节|\bnext bar\b)", re.I)
_OTHER_POSITION = re.compile(
    r"(?:下(?:一)?个?小节后|小节之后|小节后|第[零一二三四五六七八九十\d]+小节|进副歌时|到副歌时"
    r"|过[零一二两三四五六七八九十\d]+小节|再演[零一二两三四五六七八九十\d]+小节"
    r"|\bafter (?:\d+|one|two|three|four) bars?\b|\bat bar \d+\b)", re.I
)
_ARRANGEMENT = {"INTRODUCE_LAYER", "SET_TEXTURE", "SET_PROGRESSION", "ECHO_MOTIF", "VARY_MOTIF", "INTRODUCE_MOTIF"}


def target_position(text, current_bar):
    if _OTHER_POSITION.search(text):
        return "unsupported", None
    if _NEXT.search(text):
        return "next", current_bar + 1
    return "flexible", None


def can_wait(plan, state):
    data = plan.to_dict()
    if data["transport"].get("playing", state.playing) != state.playing:
        return False
    actions = [action["action"] for action in data["actions"] if action["action"] != "PRESERVE"]
    return bool(_ARRANGEMENT.intersection(actions) or len(actions) > 1)


@dataclass(frozen=True)
class LiveTurnTiming:
    soft_bar: int
    soft_sample: int
    hard_sample: int
    hard_at: float
    target_bar: int | None
