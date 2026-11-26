from .acceptance import verdict

def test_missing_evidence_is_not_a_pass():
    v=verdict({'status':'completed','audio_s':20,'generation_rtf':.5,'generation_ms_p95':20})
    assert v['generation_rtf']=='PASS'
    assert v['playback']=='NOT_MEASURED'
    assert v['physical_loopback']=='NOT_MEASURED'
    assert v['stability_300s']=='FAIL'

def test_gpu_failure_never_becomes_performance_failure():
    assert verdict({'status':'blocked','error':'No GPU'})['execution']=='BLOCKED'

def test_deadlines_and_underruns_fail():
    v=verdict({'status':'completed','generation_rtf':1.2,'generation_ms_p95':60,'play_requested':True,
               'playback':{'steady_empty':2,'device_underflows':1}})
    for key in ['generation_rtf','frame_p95','playback','device_underflows']: assert v[key]=='FAIL'
