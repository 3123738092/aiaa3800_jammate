"""Preflight then matched repeated trials; all failures stay in reports."""
import json,subprocess,sys
from .run import ROOT

def run(args):
    return subprocess.run([sys.executable,'-m','mrt2_demo.run',*args],check=False).returncode

def main():
    out=ROOT/'results'/'suite';out.mkdir(parents=True,exist_ok=True)
    code=run(['--seconds','1','--mode','text','--play','--out',str(out/'preflight')])
    if code:
        (out/'suite.json').write_text(json.dumps({'status':'blocked','reason':'Preflight failed; no expensive tests launched'}))
    else:
        subprocess.run([sys.executable,'-m','mrt2_demo.baseline'],check=True)
        failures=[]
        for mode in ['text','midi','audio','update']:
            for trial,seed in enumerate([42,43,44],1):
                args=['--mode',mode,'--seed',str(seed),'--seconds','20','--play','--out',str(out/f'{mode}-{trial}')]
                if mode=='audio':args+=['--audio',str(ROOT/'results/midi-baseline.wav')]
                if run(args): failures.append(f'{mode}-{trial}')
        if run(['--mode','midi','--seconds','300','--play','--out',str(out/'stability')]):failures.append('stability')
        (out/'suite.json').write_text(json.dumps({'status':'completed_with_failures' if failures else 'executed','failed_runs':failures}))
    subprocess.run([sys.executable,'-m','mrt2_demo.acceptance',str(out)],check=True)
    return code
if __name__=='__main__':raise SystemExit(main())
