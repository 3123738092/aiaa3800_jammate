from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from jammate.browser_jam import BrowserJam
from jammate.contracts.adapters import from_humming, from_text
from jammate.generate.arranger import chord_for_bar
from jammate.interaction import Layer
from jammate.music import ChordStep
from jammate.orchestration.agent import RuleAgent
from jammate.orchestration.context import select_state
from jammate.orchestration.jam_opening import prepare_opening
from jammate.orchestration.offline import prepare_bar
from jammate.rhythm import RhythmContext
from jammate.state import JamState


def humming(session_id, take, pitches, onsets=(0., .3, .75, 1.1)):
    notes = [
        {"id": f"n{i}", "pitchMidi": pitch, "startSec": start, "durationSec": .2,
         "velocity01": .7, "sourceNoteIds": [f"n{i}"]}
        for i, (pitch, start) in enumerate(zip(pitches, onsets, strict=True))
    ]
    return from_humming({
        "schemaVersion": "1.0.0", "id": take, "status": "ready",
        "source": {"id": take, "durationSec": max(2., max(onsets) + .2), "kind": "recording"},
        "timebase": {"unit": "seconds", "origin": "original_audio_start"},
        "tempo": {"bpm": None, "source": "unknown", "mode": "free", "quantized": False},
        "key": None, "timeSignature": None, "model": {"name": "fixture"},
        "selectedResult": "cleaned", "notes": notes,
        "results": {"raw": {"notes": notes}, "cleaned": {"notes": notes}},
    }, session_id=session_id, event_id=take)


def open_jam(workers, event=None):
    state = JamState(session_id="motif-jam", rhythm=RhythmContext(96, (0., .5, 1., 2., 3.), (1.,) * 5),
                     chord_cycle=(ChordStep(0, "maj"), ChordStep(5, "maj")))
    text = from_text("96 BPM，C 大调", session_id=state.session_id)
    events = (text,) if event is None else (text, event)
    opening = state, events, {"reason": "Fixture", "instruments": []}
    return BrowserJam(96, RuleAgent, workers, opening=opening)


def for_layer(notes, layer):
    return tuple(note for note in notes if note.instrument == layer)


def test_humming_drives_the_backing_after_the_echo_without_changing_the_groove():
    with ThreadPoolExecutor(1) as workers:
        up = open_jam(workers, humming("motif-jam", "up", (60, 64, 67, 69)))
        down = open_jam(workers, humming("motif-jam", "down", (67, 64, 62, 60), (0., .55, .9, 1.3)))
        text = open_jam(workers)
        takes = [jam.runtime.advance_to(22050 * 5) for jam in (up, down, text)]
        for (opening, middle, later), jam in zip(takes, (up, down, text), strict=True):
            assert for_layer(opening.notes, "lead") or jam is text
            assert not for_layer(middle.notes, "lead") and not for_layer(later.notes, "lead")
            state = jam.manager.state
            assert state.tempo_bpm == 96 and state.key.name == "C major"
            assert chord_for_bar(state, later.timing.bar_index).name == "C"
            assert JamState.from_dict(state.to_dict()) == state
        up_notes, down_notes, text_notes = (bars[-1].notes for bars in takes)
        assert for_layer(up_notes, "drums") == for_layer(down_notes, "drums") == for_layer(text_notes, "drums")
        for layer in ("bass", "keys"):
            assert for_layer(up_notes, layer) != for_layer(down_notes, layer)
            assert for_layer(up_notes, layer) != for_layer(text_notes, layer)
        assert [n.pitch for n in for_layer(up_notes, "bass")] != [n.pitch for n in for_layer(down_notes, "bass")]
        assert text_notes == prepare_bar(text.manager.state, 2)
        assert "accompaniment" not in text.manager.state.to_dict()["motifs"]


def test_only_a_new_phrase_with_contour_replaces_the_backing_motif():
    with ThreadPoolExecutor(1) as workers:
        jam = open_jam(workers, humming("motif-jam", "theme", (60, 64, 67, 69), (0., .75, 2.6, 4.1)))
        jam.reserve()
        jam.poll(0, 0, 0)
        initial = jam.manager.state.accompaniment_motif
        assert initial.motif_id == "motif_theme" and initial.start_bar == 0
        jam.submit_event(humming("motif-jam", "one-pitch", (64, 64, 64, 64)))
        jam.future.result(timeout=3)
        assert jam.manager.state.accompaniment_motif == initial
        view, _ = select_state((), jam.manager.state, motif_limit=1)
        assert view.motif(initial.motif_id).motif_id == initial.motif_id
        jam.submit_event(humming("motif-jam", "replacement", (64, 69, 71, 72)))
        jam.future.result(timeout=3)
        assert jam.manager.state.accompaniment_motif.motif_id == "motif_replacement"
        assert jam.manager.state.accompaniment_motif.start_bar == 1
        assert jam.feedback["status"] == "scheduled"
        queued = jam.runtime.preview_next_bar()
        assert queued.timing.bar_index == 1 and for_layer(queued.notes, "lead")
        assert for_layer(queued.notes, "lead")[0].pitch == 64


def test_stream_continues_the_played_phrase_and_restores_the_prior_backing():
    def unexpected_provider():
        raise AssertionError("an explicit audio operation does not need the model to choose its action")

    initial = JamState(session_id="motif-jam", layers=(Layer("drums", density=.8), Layer("bass", density=.9),
                                                     Layer("keys"), Layer("lead", active=False)))
    opening = initial, (), {"reason": "Fixture", "instruments": []}
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, unexpected_provider, workers, renderer="mrt2_stream", opening=opening)
        try:
            runtime = jam.runtime
            runtime.advance_to(0)
            event = from_text("接着我上传的这段哼唱往下发展", session_id=jam.id)
            jam.submit_event(event, humming(jam.id, "user-take", (60, 64, 67, 69)))
            jam.future.result(timeout=3)
            assert jam.manager.state.accompaniment_motif is None
            played = runtime.preview_next_bar()
            assert for_layer(played.notes, "keys") == for_layer(prepare_bar(initial, 1), "keys")
            runtime.advance_to(played.timing.start_sample)
            phrase = jam.manager.state.last_ai_motif
            assert [n.pitch % 12 for n in phrase] != [n.pitch % 12 for n in jam.manager.state.last_user_motif]
            while runtime.preview_next_bar().plan is not None:
                runtime.advance_to(runtime.preview_next_bar().timing.start_sample)

            jam.submit("保持刚才的旋律，把伴奏减薄。")
            jam.future.result(timeout=3)
            thinner = runtime.preview_next_bar()
            assert [a["action"] for a in thinner.plan.actions] == ["SIMPLIFY", "ECHO_MOTIF"]
            assert for_layer(thinner.notes, "lead")
            runtime.advance_to(thinner.timing.start_sample)
            assert jam.manager.state.last_ai_motif == phrase
            assert jam.manager.state.layer("drums").density == .55

            echo = from_text("先照着我上传的这段哼唱弹，不要自由发挥。", session_id=jam.id)
            jam.submit_event(echo, humming(jam.id, "echo-take", (64, 60, 62, 67)))
            jam.future.result(timeout=3)
            replayed = runtime.preview_next_bar()
            assert [a["action"] for a in replayed.plan.actions] == ["ECHO_MOTIF"]
            runtime.advance_to(replayed.timing.start_sample)
            assert [n.pitch for n in jam.manager.state.last_ai_motif] == [64, 60, 62, 67]

            jam.submit("恢复刚才的干净伴奏。")
            jam.future.result(timeout=3)
            restored = runtime.preview_next_bar()
            assert jam.manager.state.layer("drums").density == .55
            assert restored.state.layers == initial.layers
            assert restored.notes == prepare_bar(initial, restored.timing.bar_index)
            confirmed, = runtime.advance_to(restored.timing.start_sample)
            assert confirmed.started.plan_id == restored.plan.plan_id
            assert jam.manager.state.layers == initial.layers
        finally:
            jam.close()


def test_humming_completion_plays_a_new_phrase_after_the_unchanged_replay():
    text = from_text("D 大调，108 BPM，和弦进行设为 D-G-A-D", session_id="motif-jam")
    sources = ((60, 66, 69, 74), (62, 64, 66, 69))
    answers = []
    for i, pitches in enumerate(sources):
        event = humming("motif-jam", f"take-{i}", pitches, onsets=(0., .45, 1.15, 2.0))
        events = text, event
        state, selection = prepare_opening(events, None, {}, mode="humming")
        assert state.key.name == "D major" and state.tempo_bpm == 108
        assert selection["mode"] == "humming" and selection["model"] is None
        assert chord_for_bar(state, 1).name == "G"
        with ThreadPoolExecutor(1) as workers:
            jam = BrowserJam(108, RuleAgent, workers, opening=(state, events, selection))
            bars = jam.runtime.advance_to(22050 * 12)
            lead = tuple(note for bar in bars for note in for_layer(bar.notes, "lead"))
            original = jam.manager.state.last_ai_motif
            assert lead[:len(pitches)] == original
            assert tuple(note.pitch for note in original) == pitches
            answer = lead[len(pitches):]
            assert answer and answer == jam.opening_answer
            assert 0 <= answer[0].start - max(note.end for note in original) < .5
            assert all(state.key.contains(note.pitch) for note in answer)
            assert answer[-1].pitch % 12 in chord_for_bar(state, int(answer[-1].start // 4)).pcs
            assert jam.opening["melody_continuation"]["notes"] == [asdict(note) for note in answer]
            assert not for_layer(bars[-1].notes, "lead") and for_layer(bars[-1].notes, "keys")
            answers.append(answer)
    assert answers[0] != answers[1]


def test_humming_completion_requires_a_melodic_phrase():
    text = from_text("C 大调", session_id="motif-jam")
    flat = humming("motif-jam", "flat", (64, 64, 64, 64))
    with pytest.raises(ValueError, match="请先录制或上传哼唱"):
        prepare_opening((text,), None, {}, mode="humming")
    with pytest.raises(ValueError, match="至少有三个起音和两个不同音高"):
        prepare_opening((flat,), None, {}, mode="humming")


def test_low_humming_stays_in_register_and_enriches_the_post_replay_backing():
    event = humming("motif-jam", "low-voice", (42, 49, 48, 39), onsets=(0., .43, 1.14, 2.1))
    text = from_text("F# 大调，96 BPM，和弦进行设为 D#m-B-F#-C#", session_id="motif-jam")
    events = event, text
    state, selection = prepare_opening(events, None, {}, mode="humming")
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, RuleAgent, workers, opening=(state, events, selection))
        bars = jam.runtime.advance_to(22050 * 18)
        assert abs(jam.opening_answer[0].pitch - 39) <= 7
        assert all(note.pitch <= 51 and state.key.contains(note.pitch) for note in jam.opening_answer)
        after = bars[-1].notes
        assert not for_layer(after, "lead")
        assert all(note.duration >= 3 for note in for_layer(after, "strings"))
        assert len(for_layer(after, "strings")) == 2
        assert len({note.start for note in for_layer(after, "keys")}) >= 4
        assert len(for_layer(after, "bass")) == 3
