"""MediaPipe canned gestures -> intents.

The default Gesture Recognizer only knows seven hand shapes (Closed_Fist,
Open_Palm, Pointing_Up, Thumb_Down, Thumb_Up, Victory, ILoveYou), so band
directions are mapped onto those. Continuous cues (tempo by nodding,
intensity by arm motion) need the Pose Landmarker and are future work.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from jammate.director.commands import Intent

DEFAULT_GESTURE_MAP: Mapping[str, Intent] = {
    "Thumb_Up": Intent.KEEP,
    "Thumb_Down": Intent.VARY,
    "Open_Palm": Intent.BREAK,  # the classic "hold it" hand
    "Closed_Fist": Intent.MORE_ENERGY,
    "Pointing_Up": Intent.BUILD,
    "Victory": Intent.NEW_IDEA,
    "ILoveYou": Intent.KEEP,
}


def intents_from_gesture(
    name: str, mapping: Mapping[str, Intent] = DEFAULT_GESTURE_MAP
) -> tuple[Intent, ...]:
    intent = mapping.get(name)
    return (intent,) if intent is not None else ()


def stable_gesture(history: Sequence[str], min_frames: int) -> str | None:
    """The gesture held for the last `min_frames` frames, ignoring 'None'."""
    if len(history) < min_frames:
        return None
    recent = set(history[-min_frames:])
    if len(recent) != 1:
        return None
    (name,) = recent
    return None if name == "None" else name
