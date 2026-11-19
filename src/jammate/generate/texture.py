"""Render the independent arrangement controls into bar-relative notes."""
from dataclasses import replace

from jammate.generate.patterns import BASS_LOW, CLOSED_HAT, COMP_LOW, KICK
from jammate.music import BEATS_PER_BAR, Note


def _drums(pattern, anchors):
    if pattern == "sparse":
        beats = (anchors[0], anchors[len(anchors) // 2]) if len(anchors) > 1 else anchors or (0., 2.5)
    elif pattern == "steady":
        beats = anchors or (0., 1., 2., 3.)
    else:
        beats = anchors or (.5, 1.5, 2.5, 3.5)
    return tuple(Note(
        KICK if index == 0 and pattern != "offbeat" else CLOSED_HAT,
        beat, min(.2, BEATS_PER_BAR - beat), 70 if index == 0 else 50, "drums",
    ) for index, beat in enumerate(beats))


def _bass(pattern, chord):
    root, third, fifth = chord.voicing(BASS_LOW)[:3]
    patterns = {
        "drone": ((root, 0., 3.75),),
        "walking": ((root, 0., .85), (third, 1., .85), (fifth, 2., .85), (root + 12, 3., .85)),
        "syncopated": ((root, 0., 1.25), (fifth, 1.5, .5), (root, 2.75, 1.)),
    }
    return tuple(Note(pitch, start, duration, 77, "bass") for pitch, start, duration in patterns[pattern])


def _keys(pattern, chord, anchors, preserve_harmony):
    voicing = chord.voicing(COMP_LOW)
    root, third, fifth = voicing[:3]
    if pattern == "open":
        pitches = (root, fifth, root + 12)
        if preserve_harmony:
            pitches += tuple(pitch + 12 for pitch in (third, *voicing[3:]))
        return tuple(Note(pitch, 0., 3.75, 62, "keys") for pitch in pitches)
    if pattern == "arpeggio":
        pitches = (root, fifth, third, *voicing[3:], root + 12, fifth)
        beats = anchors or (0., .75, 1.5, 2.5, 3.25)
        return tuple(Note(pitches[index % len(pitches)], beat, min(.4, BEATS_PER_BAR - beat), 69, "keys")
                     for index, beat in enumerate(beats))
    return tuple(Note(pitch, beat, min(.25, BEATS_PER_BAR - beat), 65, "keys")
                 for beat in anchors or (.5, 1.75, 3.) for pitch in voicing)


def apply_texture(state, chord, backing):
    texture = state.texture
    anchors = state.rhythm.onset_beats if state.rhythm is not None else ()
    by_layer = {
        "drums": lambda: _drums(texture.drums, anchors),
        "bass": lambda: _bass(texture.bass, chord),
        "keys": lambda: _keys(texture.keys, chord, anchors, bool(state.chord_cycle)),
    }
    changed = {name for name in by_layer if getattr(texture, name) != "default"}
    notes = tuple(note for note in backing if note.instrument not in changed)
    for name, generate in by_layer.items():
        if name in changed:
            notes += generate()
    if texture.scale == "default":
        return notes
    if texture.scale == "blues":
        steps = (0, 3, 5, 6, 7, 10)
    else:
        steps = (0, 2, 4, 7, 9) if state.key.mode == "major" else (0, 3, 5, 7, 10)
    palette = {(state.key.tonic + step) % 12 for step in steps}
    if state.chord_cycle:
        palette.update(chord.pcs)
    return tuple(dict.fromkeys(
        note if note.instrument == "drums" or note.pitch % 12 in palette else
        replace(note, pitch=min(
            (candidate for candidate in range(max(0, note.pitch - 3), min(127, note.pitch + 3) + 1)
             if candidate % 12 in palette),
            key=lambda candidate: (abs(candidate - note.pitch), candidate),
        ))
        for note in notes
    ))
