"""MRT2 frame streaming demo, real measurements only. See README.md."""
import argparse, json, os, platform, queue, sys, threading, time
from .streaming import PlaybackBuffer, StyleWorker
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--seconds',type=float,default=300)
    p.add_argument('--mode',choices=['text','midi','audio','update'],default='midi')
    p.add_argument('--backend',choices=['mlx','jax-cpu'],default='mlx')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--prompt',default='warm funk ensemble, 96 BPM, C major')
    p.add_argument('--audio',type=Path)
    p.add_argument('--fixture',choices=['a','b','c-straight','c-swing','d'],
                   help='preserve experiment: explicit ground-truth notes, bypasses BandPlan')
    p.add_argument('--play',action='store_true')
    p.add_argument('--interactive',action='store_true')
    p.add_argument('--out',type=Path,default=ROOT/'results'/'trial')
    args=p.parse_args()
    if not __import__('math').isfinite(args.seconds) or args.seconds<.04 or args.seconds>3600: p.error('seconds must be in (0,3600]')
    if args.mode=='audio' and not args.audio: p.error('--audio is required')
    args.out.mkdir(parents=True,exist_ok=True)
    report={'status':'starting','model':'mrt2_small','mode':args.mode,'platform':platform.platform(),
            'machine':platform.machine(),'speaker_loopback_latency_ms':None,
            'perceptual_response_ms':None,'subjective_score':None,
            'seed':args.seed,'requested_s':args.seconds,'play_requested':args.play,
            'sample_rate':48000,'block_size':1920,'bpm':96,'schema_version':2,'backend':args.backend}
    rows=[]; events=[]; samples=[]; worker=None; stream=None
    start=time.perf_counter()
    try:
        import numpy as np
        import soundfile as sf
        if args.backend=='jax-cpu':
            os.environ['JAX_PLATFORMS']='cpu'
            import jax
            from magenta_rt import MagentaRT2Jax as Model
            report['devices']=[str(device) for device in jax.devices()]
            if any(device.platform!='cpu' for device in jax.devices()):
                raise RuntimeError('CPU-only request selected a non-CPU device')
            # Official JAX implementation currently hardcodes its sampler PRNG.
            report['sampling_seed_note']='Official JAX sampler uses PRNGKey(0); CLI seed is not applied to JAX sampling'
        else:
            import mlx.core as mx
            from magenta_rt import MagentaRT2StdMlxfn as Model
            mx.random.seed(args.seed)
            # Measured: the exported mlxfn sampler ignores mx.random.seed;
            # identical conditioning yields bit-identical audio for any seed.
            report['sampling_seed_note']='mlxfn sampler ignores mx.random.seed; same conditioning gives bit-identical output for any CLI seed'
            report['devices']=[str(mx.default_device())]
        from .analyze_audio import analyze
        from .adapter import MRT2Adapter
        from . import fixtures as fx
        from .inference import FRAME_RATE, STYLE_KEY, encode_style, generate_frame
        from magenta_rt.audio import Waveform
        from importlib.metadata import version
        report['versions']={k:version(k) for k in ['magenta-rt','jax' if args.backend=='jax-cpu' else 'mlx','sounddevice']}
        t=time.perf_counter()
        model=Model(size='mrt2_small')
        report['load_and_warmup_s']=time.perf_counter()-t
        def embed(value):
            return encode_style(model, value)
        t=time.perf_counter()
        tokens=embed(Waveform.from_file(str(args.audio)) if args.mode=='audio' else args.prompt)
        report['initial_embedding_s']=time.perf_counter()-t
        worker=StyleWorker(lambda kind,value: embed(Waveform.from_file(value) if kind=='audio' else value))
        commands=queue.Queue()
        def console():
            print('Commands: text YOUR PROMPT | audio PATH | quit',flush=True)
            for line in sys.stdin:
                commands.put((time.perf_counter(),line.strip()))
        if args.interactive: threading.Thread(target=console,daemon=True).start()
        if args.fixture:
            from dataclasses import asdict
            notes=fx.FIXTURES[args.fixture]()
            adapter=MRT2Adapter.from_notes(notes,fx.BPM,args.seconds)
            report['fixture']=args.fixture
            report['adapter_warnings']=adapter.warnings
            (args.out/'input-notes.json').write_text(json.dumps([asdict(n) for n in notes],indent=2))
        else:
            adapter=MRT2Adapter(*fx.demo_plan())
            report['adapter_warnings']=adapter.warnings
        phrase_frames = round((adapter.end_s - adapter.start_s) * FRAME_RATE)
        playback=PlaybackBuffer(capacity=3)
        if args.play:
            import sounddevice as sd
            report['output_device']=sd.query_devices(kind='output')
            stream=sd.OutputStream(samplerate=48000,channels=2,blocksize=1920,dtype='float32',callback=playback.callback)
        cache=None
        report['ready_after_s']=time.perf_counter()-start
        begin=time.perf_counter()
        try:
            for i in range(round(args.seconds*25)):
                if args.mode=='update' and i==round(args.seconds*25)//2:
                    worker.submit('text','sparse ambient piano, 96 BPM, C major')
                if not commands.empty():
                    received,line=commands.get_nowait()
                    if line=='quit': break
                    if line.startswith('text ') or line.startswith('audio '):
                        v=line.split(' ',1)[1]
                        worker.submit(line.split(' ',1)[0],v)
                prepared=worker.poll()
                if prepared:
                    event={k:v for k,v in prepared.items() if k!='tokens'}
                    event['frame']=i
                    if not prepared['error']:
                        tokens=prepared['tokens']
                        event['applied_monotonic_s']=time.perf_counter()
                        event['input_to_conditioning_ms']=(event['applied_monotonic_s']-prepared['received'])*1000
                    events.append(event)
                cond={STYLE_KEY:tokens}
                if args.mode=='midi':
                    # Explicit note fixtures already contain their full timeline.
                    frame = i if args.fixture else i % phrase_frames
                    if i and frame == 0:
                        adapter.previous.clear()
                    cond.update(adapter.frame(adapter.start_s + frame / FRAME_RATE))
                t=time.perf_counter()
                block,cache=generate_frame(model, cond, cache)
                elapsed=(time.perf_counter()-t)*1000
                generated_at=time.perf_counter()
                if i==0: report['ready_to_first_pcm_ms']=(generated_at-begin)*1000
                rows.append({'frame':i,'audio_s':i*.04,'generation_ms':elapsed,'generated_at':generated_at})
                samples.append(block)
                if stream:
                    playback.put(block,i,generated_at,timeout=5)
                    if not stream.active and (playback.queue.qsize()>=3 or i==round(args.seconds*25)-1): stream.start()
                if i%250==0: print(f'frame={i} generate_ms={elapsed:.2f}',flush=True)
            if stream:
                if not stream.active and not playback.queue.empty(): stream.start()
                playback.finish()
                if not playback.consumed.wait(timeout=5): raise TimeoutError('Playback drain timed out')
        finally:
            if stream: stream.stop();stream.close()
        if not samples:
            report['status']='cancelled'
            return
        audio=np.concatenate(samples)
        sf.write(args.out/'audio.wav',audio,48000,subtype='PCM_16')
        (args.out/'audio.analysis.json').write_text(json.dumps(analyze(audio,48000),indent=2))
        ms=np.array([r['generation_ms'] for r in rows])
        report.update(status='completed' if len(rows)==round(args.seconds*25) else 'cancelled',frames=len(rows),audio_s=len(audio)/48000,
            wall_s=time.perf_counter()-begin,generation_ms_p50=float(np.median(ms)),
            generation_ms_p95=float(np.percentile(ms,95)),generation_ms_max=float(ms.max()),
            generation_rtf=float(ms.sum()/1000/(len(audio)/48000)),deadline_misses=int((ms>40).sum()),
            peak=float(np.abs(audio).max()),rms=float(np.sqrt(np.mean(audio**2))),
            clipped_samples=int((np.abs(audio)>=1).sum()),playback=playback.metrics,events=events)
        report['estimated_ready_to_first_dac_ms']=(playback.dac_events[0]['estimated_dac_monotonic_s']-begin)*1000 if playback.dac_events else None
        for event in events:
            dac=next((v for v in playback.dac_events if v['frame']==event['frame']),None)
            if dac and not event.get('error'):
                event['input_to_estimated_dac_ms']=(dac['estimated_dac_monotonic_s']-event['received'])*1000
        (args.out/'playback.json').write_text(json.dumps(playback.dac_events,indent=2))
        (args.out/'frames.json').write_text(json.dumps(rows,indent=2))
    except Exception as e:
        report.update(status='blocked',error=f'{type(e).__name__}: {e}')
        raise
    finally:
        if worker: worker.close()
        report['events']=events
        report['measured_frames']=len(rows)
        (args.out/'frames.json').write_text(json.dumps(rows,indent=2))
        (args.out/'report.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__': main()
