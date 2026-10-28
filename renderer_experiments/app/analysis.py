from __future__ import annotations

from pathlib import Path
from typing import Any


def analyze_audio(path: Path) -> dict[str, Any]:
    """Estimate coarse rhythmic, tonal and spectral features with librosa."""
    import librosa
    import numpy as np

    audio, sample_rate = librosa.load(path, sr=None, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=audio, sr=sample_rate)
    tempo_value = float(np.asarray(tempo).reshape(-1)[0])
    chroma = librosa.feature.chroma_cqt(y=audio, sr=sample_rate)
    pitch_class = int(np.argmax(chroma.mean(axis=1)))
    key_names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    onset_frames = librosa.onset.onset_detect(y=audio, sr=sample_rate)
    duration = len(audio) / sample_rate
    rms = librosa.feature.rms(y=audio)[0]
    centroid = librosa.feature.spectral_centroid(y=audio, sr=sample_rate)[0]
    beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate)
    intervals = np.diff(beat_times)
    return {
        "duration_seconds": round(duration, 3),
        "estimated_bpm": round(tempo_value, 3),
        "estimated_key_pitch_class": key_names[pitch_class],
        "estimated_key_method": "dominant_chroma_pitch_class; mode not inferred",
        "onset_count": int(len(onset_frames)),
        "onset_density_per_second": round(len(onset_frames) / duration, 3) if duration else 0,
        "mean_rms": round(float(np.mean(rms)), 6),
        "mean_spectral_centroid_hz": round(float(np.mean(centroid)), 3),
        "beat_interval_std_seconds": round(float(np.std(intervals)), 5) if len(intervals) else None,
    }
