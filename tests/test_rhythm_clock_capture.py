import numpy as np
import pytest

from jammate.analysis.rhythm_capture import RhythmCapture
from jammate.render.clock import BarClock, BarTiming

pytestmark = pytest.mark.unit


def test_fractional_sample_rounding_does_not_accumulate_over_long_sessions():
    clock = BarClock(22050, 11025)
    for _ in range(10000):
        clock.reserve(137)
    expected = 11025 + 10000 * 4 * 60 / 137 * 22050
    assert abs(clock.next_sample - expected) <= .5


def test_tempo_change_starts_at_the_old_bar_end():
    clock = BarClock(1000)
    first = clock.schedule(100)
    second = clock.schedule(120)
    assert first.end_sample == second.start_sample == 2400
    assert second.end_sample == 4400


def test_render_overrun_skips_whole_bars_preserving_phase():
    clock = BarClock(1000, 500)
    first = clock.schedule(120)
    late = clock.schedule(100, read_sample=2600)
    assert first.end_sample == 2500
    assert late.bar_index == 2 and late.start_sample == 4500
    assert late.end_sample == 6900


def test_capture_splits_blocks_across_bars_without_shifting_samples():
    timings = (BarTiming(0, 4, 12, 120), BarTiming(1, 12, 20, 120))
    capture = RhythmCapture()
    completed = []
    for start, end in [(0, 7), (7, 15), (15, 23)]:
        completed += capture.feed(start, np.arange(start, end), timings)
    assert [t.bar_index for t, _ in completed] == [0, 1]
    assert completed[0][1].tolist() == list(range(4, 12))
    assert completed[1][1].tolist() == list(range(12, 20))


def test_missing_audio_invalidates_one_bar_and_next_complete_bar_recovers():
    timings = (BarTiming(0, 0, 10, 120), BarTiming(1, 10, 20, 120))
    capture = RhythmCapture()
    assert capture.feed(0, np.zeros(4), timings) == []
    assert capture.feed(5, np.zeros(5), timings) == []
    done = capture.feed(10, np.ones(10), timings)
    assert len(done) == 1 and done[0][0].bar_index == 1


def test_starting_halfway_through_a_bar_does_not_invent_a_downbeat():
    capture = RhythmCapture()
    assert capture.feed(4, np.ones(6), (BarTiming(0, 0, 10, 120),)) == []
