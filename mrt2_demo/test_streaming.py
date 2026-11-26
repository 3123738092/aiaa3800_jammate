from types import SimpleNamespace
import threading,time
import numpy as np
from .streaming import PlaybackBuffer,StyleWorker

def tick(buffer):
    out=np.ones((1920,2),dtype=np.float32)
    buffer.callback(out,1920,SimpleNamespace(currentTime=10,outputBufferDacTime=10.04),SimpleNamespace(output_underflow=False))
    return out

def test_startup_steady_and_drain_are_separate():
    b=PlaybackBuffer(); assert not tick(b).any()
    b.put(np.ones((1920,2)),0,time.perf_counter()); assert np.all(tick(b)==.25)
    tick(b); b.finish(); tick(b)
    assert b.metrics==dict(startup_empty=1,steady_empty=1,drain_empty=1,device_underflows=0,played_frames=1)
    assert b.consumed.is_set()
    assert len(b.dac_events)==1

def test_buffer_backpressure():
    import queue,pytest
    b=PlaybackBuffer(1); b.put(np.zeros((1920,2)),0,0)
    with pytest.raises(queue.Full): b.put(np.zeros((1920,2)),1,0,timeout=.01)

def test_slow_encoder_does_not_block_and_stale_result_is_dropped():
    entered=threading.Event();release=threading.Event()
    def prepare(kind,value):
        if value=='old': entered.set(); release.wait(2)
        return value
    w=StyleWorker(prepare)
    try:
        w.submit('text','old'); assert entered.wait(1)
        w.submit('text','new'); assert w.poll() is None
        release.set()
        end=time.monotonic()+2; result=None
        while time.monotonic()<end and result is None:
            result=w.poll(); time.sleep(.01)
        assert result['tokens']=='new'
    finally: release.set(); w.close()

def test_bad_audio_error_does_not_kill_worker():
    def prepare(kind,value): raise FileNotFoundError(value)
    w=StyleWorker(prepare)
    try:
        w.submit('audio','missing.wav'); end=time.monotonic()+2; result=None
        while time.monotonic()<end and result is None:
            result=w.poll(); time.sleep(.01)
        assert 'FileNotFoundError' in result['error']
    finally: w.close()
