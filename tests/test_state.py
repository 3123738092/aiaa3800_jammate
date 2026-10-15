import dataclasses

import pytest

from jammate.music import Key
from jammate.state import MAX_ENERGY, MAX_TEMPO_BPM, MIN_TEMPO_BPM, JamState

pytestmark = pytest.mark.unit


def test_defaults_are_a_playable_groove():
    state = JamState()
    assert state.playing
    assert state.key == Key(0, "major")
    assert 0 <= state.energy <= MAX_ENERGY


def test_state_is_immutable():
    state = JamState()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.energy = 2  # type: ignore[misc]


def test_with_tempo_clamps_and_returns_new_object():
    state = JamState(tempo_bpm=100.0)
    assert state.with_tempo(500.0).tempo_bpm == MAX_TEMPO_BPM
    assert state.with_tempo(10.0).tempo_bpm == MIN_TEMPO_BPM
    assert state.tempo_bpm == 100.0


def test_with_energy_clamps():
    state = JamState(energy=1)
    assert state.with_energy(9).energy == MAX_ENERGY
    assert state.with_energy(-3).energy == 0


@pytest.mark.parametrize("kwargs", [{"tempo_bpm": 20.0}, {"energy": 7}])
def test_constructor_rejects_out_of_range_values(kwargs):
    with pytest.raises(ValueError):
        JamState(**kwargs)
