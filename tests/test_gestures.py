import pytest

from jammate.director.commands import Intent
from jammate.director.gestures import intents_from_gesture, stable_gesture

pytestmark = pytest.mark.unit


def test_canned_gestures_map_to_intents():
    assert intents_from_gesture("Thumb_Up") == (Intent.KEEP,)
    assert intents_from_gesture("Open_Palm") == (Intent.BREAK,)
    assert intents_from_gesture("None") == ()
    assert intents_from_gesture("Unknown_Sign") == ()


def test_custom_mapping_overrides_defaults():
    mapping = {"Victory": Intent.FASTER}
    assert intents_from_gesture("Victory", mapping) == (Intent.FASTER,)
    assert intents_from_gesture("Thumb_Up", mapping) == ()


def test_gesture_must_be_held_to_count():
    assert stable_gesture(["Open_Palm"] * 5, min_frames=5) == "Open_Palm"
    assert stable_gesture(["Open_Palm"] * 4 + ["None"], min_frames=5) is None
    assert stable_gesture(["Open_Palm", "Thumb_Up"] * 3, min_frames=5) is None
    assert stable_gesture(["Thumb_Up"] * 3, min_frames=5) is None
    assert stable_gesture(["None"] * 6, min_frames=5) is None
