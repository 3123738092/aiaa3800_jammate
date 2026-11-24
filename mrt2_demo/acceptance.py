"""Evidence-based acceptance: missing measurements never become a pass."""
import argparse,json
from pathlib import Path

def verdict(report):
    if report.get('status')!='completed':
        return {'execution':'BLOCKED','reason':report.get('error','No completed run')}
    def gate(value,predicate):
        return 'NOT_MEASURED' if value is None else 'PASS' if predicate(value) else 'FAIL'
    playback=report.get('playback',{})
    return {
        'generation_rtf':gate(report.get('generation_rtf'),lambda v:v<1),
        'frame_p95':gate(report.get('generation_ms_p95'),lambda v:v<40),
        'playback': 'NOT_MEASURED' if not report.get('play_requested') else gate(playback.get('steady_empty'),lambda v:v==0),
        'device_underflows':'NOT_MEASURED' if not report.get('play_requested') else gate(playback.get('device_underflows'),lambda v:v==0),
        'stability_300s':gate(report.get('audio_s'),lambda v:v>=300),
        'physical_loopback':gate(report.get('speaker_loopback_latency_ms'),lambda v:v<=150),
        'perceptual_response':gate(report.get('perceptual_response_ms'),lambda v:v<=500),
        'subjective':gate(report.get('subjective_score'),lambda v:v>=3),
        'motif_groove_harmony':'NOT_MEASURED',
    }

def main():
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args()
    reports={str(f.relative_to(args.directory)):verdict(json.loads(f.read_text())) for f in args.directory.rglob('report.json')}
    output={'overall':'INCOMPLETE','thresholds':'Proposed experiment gates, not original Notion numeric requirements',
            'runs':reports,'note':'No automatic subjective or musical preservation pass. Three-renderer matched evidence still required.'}
    (args.directory/'acceptance.json').write_text(json.dumps(output,indent=2))
    print(json.dumps(output,indent=2))
if __name__=='__main__':main()
