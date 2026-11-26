"""Matched 96 BPM / 8-bar source event baseline; offline render, not latency."""
import json,time
from dataclasses import asdict
from .run import ROOT
import numpy as np
import soundfile as sf
from .adapter import MRT2Adapter
from .fixtures import BPM, demo_plan
from jammate.render.synth import render
a=MRT2Adapter(*demo_plan())
times=[]
for _ in range(3):
 t=time.perf_counter(); pcm=render(a.notes,BPM,sr=48000,total_s=20); times.append((time.perf_counter()-t)*1000)
d=ROOT/'results';d.mkdir(exist_ok=True)
sf.write(d/'midi-baseline.wav',np.column_stack([pcm,pcm]),48000)
(d/'midi-baseline.json').write_text(json.dumps(dict(mode='offline whole-clip synthesis, not speaker latency',bpm=BPM,
 seconds=20,sample_rate=48000,render_ms=times,notes=[asdict(n) for n in a.notes]),indent=2))
print(json.dumps(dict(render_ms=times,note_count=len(a.notes))))
