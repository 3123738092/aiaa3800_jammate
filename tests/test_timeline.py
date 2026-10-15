import numpy as np
import pytest

from jammate.render.timeline import AudioTimeline

pytestmark = pytest.mark.unit


def test_reading_an_empty_timeline_gives_silence():
    timeline = AudioTimeline()
    assert np.array_equal(timeline.read(4), np.zeros(4, dtype=np.float32))
    assert timeline.read_position == 4


def test_overlapping_writes_are_summed_in_place():
    timeline = AudioTimeline()
    timeline.add(2, np.ones(4, dtype=np.float32))
    timeline.add(4, np.ones(4, dtype=np.float32))
    out = timeline.read(10)
    assert out.tolist() == [0, 0, 1, 1, 2, 2, 1, 1, 0, 0]


def test_writes_into_the_past_are_trimmed():
    timeline = AudioTimeline()
    timeline.read(3)
    timeline.add(1, np.arange(1, 6, dtype=np.float32))  # samples 1..5; 1 and 2 already played
    assert timeline.read(3).tolist() == [3, 4, 5]


def test_buffered_ahead_counts_unread_audio():
    timeline = AudioTimeline()
    timeline.add(0, np.ones(100, dtype=np.float32))
    timeline.read(30)
    assert timeline.buffered_until == 100
