import pytest

from jammate.director.commands import Intent, parse_command

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("text,expected", [
    ("slow down a bit", (Intent.SLOWER,)),
    ("Drums HEAVIER please", (Intent.MORE_ENERGY,)),
    ("快一点，更有力", (Intent.FASTER, Intent.MORE_ENERGY)),
    ("停一下", (Intent.BREAK,)),
    ("停", (Intent.STOP,)),
    ("nice, keep this groove", (Intent.KEEP,)),
    ("your turn", (Intent.NEW_IDEA,)),
    ("build it up", (Intent.BUILD,)),
    ("can you change it", (Intent.VARY,)),
    ("", ()),
    ("check my stopwatch", ()),
])
def test_parse_command(text, expected):
    assert parse_command(text) == expected


def test_repeated_intents_are_deduplicated_in_order():
    assert parse_command("faster, faster! then softer") == (Intent.FASTER, Intent.LESS_ENERGY)
