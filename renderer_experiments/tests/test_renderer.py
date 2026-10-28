import pytest
from app.models import MusicIntent
from app.midi import render_pattern


def test_intent_round_trip_and_patch():
    intent = MusicIntent()
    assert intent.to_dict()["tempo"]["bpm"] == 100.0
    updated = intent.patch(tempo={"bpm": 120}, semantic={"density": 0.8})
    assert updated.tempo.bpm == 120
    assert updated.semantic.density == 0.8


def test_midi_events_are_paired_and_sorted():
    events = render_pattern(MusicIntent(), bars=2)
    assert events == sorted(events, key=lambda e: (e.timestamp, e.type == "note_on"))
    active = set()
    for event in events:
        key = (event.channel, event.note)
        if event.type == "note_on": active.add(key)
        elif event.type == "note_off":
            assert key in active
            active.remove(key)
    assert not active


def test_midi_tempo_and_chord_change_affect_events():
    base = render_pattern(MusicIntent(), bars=1)
    changed = MusicIntent().patch(tempo={"bpm": 140}, harmony={"chords": ("Fmaj7",)})
    result = render_pattern(changed, bars=1)
    assert max(e.timestamp for e in result) < max(e.timestamp for e in base)
    assert {e.note for e in result if e.type == "note_on"} != {e.note for e in base if e.type == "note_on"}


def test_invalid_range_is_rejected():
    with pytest.raises(ValueError): MusicIntent().patch(semantic={"energy": 2})
from app.presets import STYLE_PRESETS, get_style
from app.midi.patterns import render_pattern


def test_all_style_presets_render_distinct_intents():
    assert len(STYLE_PRESETS) >= 5
    outputs = {tuple((e.note, e.timestamp) for e in render_pattern(get_style(name), bars=1)) for name in STYLE_PRESETS}
    assert len(outputs) == len(STYLE_PRESETS)



def test_chord_parser_retains_distinct_roots_and_qualities():
    from app.midi.patterns import chord_notes

    assert chord_notes("Cmaj7") == (60, 64, 67, 71)
    assert chord_notes("Am7") == (69, 72, 76, 79)
    assert chord_notes("Dm7") == (62, 65, 69, 72)


def test_midi_export_writes_program_changes(tmp_path):
    import mido
    from app.midi.files import write_midi

    path = tmp_path / "programs.mid"
    write_midi(path, render_pattern(MusicIntent(), bars=1), 100, {1: 4, 2: 33})
    messages = list(mido.MidiFile(path).tracks[0])
    assert {(message.channel, message.program) for message in messages if message.type == "program_change"} == {(1, 4), (2, 33)}


def test_performance_arrangement_adds_layers_and_control_events():
    from app.midi.patterns import render_pattern

    basic = render_pattern(MusicIntent(), bars=8, arrangement="basic")
    performance = render_pattern(MusicIntent(), bars=8, arrangement="performance")
    assert len(performance) > len(basic)
    assert any(event.channel == 3 and event.type == "note_on" for event in performance)
    assert any(event.type == "control_change" for event in performance)


def test_midi_export_writes_control_changes(tmp_path):
    import mido
    from app.midi.files import write_midi
    from app.midi.patterns import render_pattern

    path = tmp_path / "performance.mid"
    write_midi(path, render_pattern(MusicIntent(), bars=2, arrangement="performance"), 100, {1: 4, 2: 33, 3: 7})
    assert any(message.type == "control_change" for message in mido.MidiFile(path).tracks[0])


def test_upstream_action_adapter_requires_baseline_and_preserves_drums():
    from app.midi.adapter import apply_render_overrides
    from app.midi.patterns import render_pattern
    from app.models import MusicIntent, Parts
    from app.upstream_intent_adapter import adapt_action_intent

    source = {
        "status": "ready", "style_prompt": "dreamy",
        "actions": [
            {"target": "bass", "operation": "add", "parameter": "instrument", "value": None, "attributes": {"dynamics": "soft"}, "timing": "next_bar"},
            {"target": "global", "operation": "set", "parameter": "mood", "value": "dreamy", "attributes": {}},
        ],
        "constraints": [{"target": "drums", "parameter": "groove", "operation": "preserve", "value": None}],
    }
    baseline = MusicIntent(parts=Parts(drums=.8, bass=0, harmony=.5), prompt="warm funk drum groove")
    adapted = adapt_action_intent(source, baseline)
    assert adapted.intent.parts.bass == .45
    assert "dreamy" in adapted.intent.semantic.mood
    assert adapted.midi_overrides["preserve_drum_events"]
    baseline_events = render_pattern(baseline, bars=2, arrangement="performance")
    adapted_events = apply_render_overrides(render_pattern(adapted.intent, bars=2, arrangement="performance"), adapted.midi_overrides)
    baseline_drums = [(e.timestamp, e.note, e.velocity, e.type) for e in baseline_events if e.channel == 9]
    adapted_drums = [(e.timestamp, e.note, e.velocity, e.type) for e in adapted_events if e.channel == 9]
    assert baseline_drums == adapted_drums
    assert any(e.channel == 2 and e.type == "note_on" for e in adapted_events)
