import numpy as np
import pytest
from .analyze_audio import analyze

def test_silence_does_not_invent_tempo_or_onsets():
    report=analyze(np.zeros((48000,2)),48000)
    assert report['detected_energy_onsets_s']==[]
    assert report['nearest_sixteenth_grid_error_ms_median'] is None

def test_grid_aligned_impulses():
    y=np.zeros((48000*2,2))
    for at in [.625,1.25,1.875]: y[round(at*48000):round(at*48000)+100]=.5
    report=analyze(y,48000)
    assert len(report['detected_energy_onsets_s'])==3
    assert report['nearest_sixteenth_grid_error_ms_median']<10

def test_invalid_pcm():
    with pytest.raises(ValueError):analyze(np.array([np.nan]),48000)
