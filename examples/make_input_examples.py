"""Reproducible synthetic audio, never a claim of human-recording accuracy."""
from pathlib import Path
import numpy as np
import soundfile as sf
from jammate.input_event import perceive_rhythm, write_event

ROOT=Path(__file__).resolve().parents[1]
sr=22050
audio=np.zeros(sr*6,dtype=np.float32)
rng=np.random.default_rng(7)
for t,a in zip([.6,1.2,1.8,2.4,3,3.6,3.9,4.8],[1,1,1,1,1,.55,.8,1]):
    start=round(t*sr);n=round(.03*sr)
    audio[start:start+n]+=rng.standard_normal(n)*np.exp(-np.linspace(0,6,n))*.25*a
sf.write(ROOT/'web/rhythm-example.wav',audio,sr)
write_event(perceive_rhythm(audio,sr,session_id='synthetic-example'),ROOT/'examples/input_events/rhythm.json')
print('Generated synthetic rhythm example and InputEvent')
