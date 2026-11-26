import pytest

from jammate.music import Note

from .adapter import MRT2Adapter
from .fixtures import demo_plan


def fixture(bar=0, preserve=()):
    return demo_plan((Note(84, 0, 1),), bar=bar, bars=1, preserve=preserve)


def test_onset_hold_release():
    a=MRT2Adapter(*fixture()); key='pianoroll_with_onsets_tokens'
    assert a.frame(0)[key][84]==2
    assert a.frame(.04)[key][84]==1
    assert a.frame(.64)[key][84]==0

def test_effective_boundary_and_expiry():
    a=MRT2Adapter(*fixture(bar=1)); key='pianoroll_with_onsets_tokens'
    assert not any(a.frame(2.48)[key])
    assert a.frame(2.52)[key][84]==2
    assert not any(a.frame(5)[key])

def test_strict_preservation_refused():
    with pytest.raises(ValueError,match='cannot guarantee'): MRT2Adapter(*fixture(preserve=['tempo']),strict=True)

def test_invalid_time():
    a=MRT2Adapter(*fixture())
    for t in [-1,float('nan'),float('inf')]:
        with pytest.raises(ValueError): a.frame(t)

def test_drums_separate_from_pitched_notes():
    a=MRT2Adapter(*fixture()); a.notes=(Note(38,0,.2,instrument='drums'),)
    f=a.frame(0)
    assert f['drum_pianoroll_tokens']==[1]
    assert not any(f['pianoroll_with_onsets_tokens'])
    assert a.frame(.2)['drum_pianoroll_tokens']==[0]

def test_repeated_pitch_retriggers_without_gap():
    a=MRT2Adapter(*fixture()); a.notes=(Note(84,0,1),Note(84,1,1))
    assert a.frame(0)['pianoroll_with_onsets_tokens'][84]==2
    assert a.frame(.60)['pianoroll_with_onsets_tokens'][84]==1
    assert a.frame(.64)['pianoroll_with_onsets_tokens'][84]==2
