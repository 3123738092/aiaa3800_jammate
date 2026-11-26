"""Audio diagnostics, explicitly not a musical-preservation certification."""
import argparse,json
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy.signal import find_peaks

def analyze(samples,sr,bpm=96):
    y=np.mean(samples,axis=1) if samples.ndim==2 else samples
    if not len(y) or not np.isfinite(y).all(): raise ValueError('Empty/nonfinite audio')
    hop=max(1,round(sr*.01)); usable=len(y)//hop*hop
    if usable==0:raise ValueError('Audio shorter than 10 ms')
    rms=np.sqrt(np.mean(y[:usable].reshape(-1,hop)**2,axis=1))
    flux=np.maximum(0,np.diff(rms,prepend=rms[0]))
    peaks,_=find_peaks(flux,height=max(float(flux.max())*.15,1e-5),distance=8)
    times=peaks*hop/sr
    beat=60/bpm
    # Report distance to the nearest sixteenth-note grid, not an inferred BPM.
    distance=np.abs(times/(beat/4)-np.round(times/(beat/4)))*(beat/4)
    return dict(duration_s=len(y)/sr,sample_rate=sr,peak=float(np.abs(samples).max()),
                rms=float(np.sqrt(np.mean(y*y))),clipped_samples=int((np.abs(samples)>=1).sum()),
                detected_energy_onsets_s=times.tolist(),
                nearest_sixteenth_grid_error_ms_median=float(np.median(distance)*1000) if len(times) else None,
                reference_bpm=bpm,interpretation='Energy-onset diagnostic only; not BPM estimation, motif recognition or preservation proof')

def main():
    p=argparse.ArgumentParser();p.add_argument('wav',type=Path);p.add_argument('--bpm',type=float,default=96);a=p.parse_args()
    if not np.isfinite(a.bpm) or a.bpm<=0:p.error('bpm must be finite and positive')
    samples,sr=sf.read(a.wav,always_2d=True)
    out=a.wav.with_suffix('.analysis.json');out.write_text(json.dumps(analyze(samples,sr,a.bpm),indent=2));print(out)
if __name__=='__main__':main()
