"""Known-answer harmonic signals; not human voices or a trained benchmark."""
import json
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'web/fixtures'
OUT.mkdir(exist_ok=True)
SR=22050
cases={
 'sustain':[(60,.3,3.5)],
 'repeated':[(60,.3,.36),(60,.74,.36),(60,1.18,.36)],
 'steps':[(60,.3,.45),(62,.85,.45),(64,1.4,.45),(67,1.95,.45)],
 'rest':[(60,.3,.6),(60,1.3,.6)],
 'short':[(60,.3,.28),(62,.65,.12),(64,.84,.28)],
}
expected={}
for name,notes in cases.items():
    duration=max(start+length for _,start,length in notes)+.4
    audio=np.zeros(round(duration*SR),dtype=np.float32)
    for pitch,start,length in notes:
        t=np.arange(round(length*SR))/SR
        vibrato=.3*np.sin(2*np.pi*5.2*t) if name=='sustain' else 0
        hz=440*2**((pitch+vibrato-69)/12)
        phase=2*np.pi*np.cumsum(np.broadcast_to(hz,t.shape))/SR
        envelope=np.minimum(np.minimum(1,t/.015),(length-t)/.025)
        signal=.2*envelope*(1+.2*np.sin(2*np.pi*3*t))*(np.sin(phase)+.24*np.sin(2*phase))
        audio[round(start*SR):round(start*SR)+len(t)]+=signal
    sf.write(OUT/f'{name}.wav',audio,SR)
    expected[name]={'notes':[{'pitch':p,'start':s,'duration':d} for p,s,d in notes],'duration':duration}
# Fixed low-volume and background-noise variants of the same known melody.
base,_=sf.read(OUT/'steps.wav',dtype='float32')
for name,signal in [('quiet',base*.1),('noisy',base+np.random.default_rng(3800).normal(0,.003,len(base)))]:
    sf.write(OUT/f'{name}.wav',signal,SR)
    expected[name]=expected['steps']
(OUT/'expected.json').write_text(json.dumps(expected,indent=2)+'\n',encoding='utf-8')
print('Generated seven synthetic humming fixtures')
