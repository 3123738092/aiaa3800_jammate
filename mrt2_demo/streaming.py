"""Bounded playback and asynchronous control preparation (no MLX import)."""
import queue
import threading
import time

class PlaybackBuffer:
    def __init__(self, capacity=3):
        self.queue = queue.Queue(maxsize=capacity)
        self.producer_done = False
        self.consumed = threading.Event()
        self.started = False
        self.metrics = dict(startup_empty=0, steady_empty=0, drain_empty=0,
                            device_underflows=0, played_frames=0)
        self.dac_events = []

    def put(self, block, frame, generated_at, timeout=5):
        self.queue.put((block,frame,generated_at), timeout=timeout)

    def finish(self):
        self.producer_done = True
        if self.queue.empty(): self.consumed.set()

    def callback(self, out, frames, timing, status):
        if status.output_underflow: self.metrics['device_underflows'] += 1
        try:
            block,frame,generated_at = self.queue.get_nowait()
        except queue.Empty:
            out.fill(0)
            phase = 'drain_empty' if self.producer_done else 'steady_empty' if self.started else 'startup_empty'
            self.metrics[phase] += 1
            if self.producer_done: self.consumed.set()
            return
        if len(block) != frames:
            out.fill(0)
            raise ValueError('Unexpected audio callback block size')
        out[:] = block * .25
        self.started = True
        self.metrics['played_frames'] += 1
        # PortAudio and perf_counter have different origins; map using callback time.
        estimated_dac = time.perf_counter() + timing.outputBufferDacTime - timing.currentTime
        self.dac_events.append(dict(frame=frame, estimated_dac_monotonic_s=estimated_dac,
                                   generated_to_estimated_dac_ms=(estimated_dac-generated_at)*1000))
        if self.producer_done and self.queue.empty(): self.consumed.set()

class StyleWorker:
    """Bounded latest-wins mailbox; model inference never waits for encoding."""
    def __init__(self, prepare):
        self.prepare = prepare
        self.requests = queue.Queue(maxsize=1)
        self.ready = queue.Queue()
        self.stop = threading.Event()
        self.version = 0
        self.thread = threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def submit(self, kind, value):
        self.version += 1
        item = (self.version,kind,value,time.perf_counter())
        try: self.requests.get_nowait()
        except queue.Empty: pass
        self.requests.put_nowait(item)
        return self.version

    def _run(self):
        while not self.stop.is_set():
            try: version,kind,value,received = self.requests.get(timeout=.1)
            except queue.Empty: continue
            try:
                result = self.prepare(kind,value)
                self.ready.put(dict(version=version,kind=kind,tokens=result,received=received,
                                    prepared=time.perf_counter(),error=None))
            except Exception as exc:
                self.ready.put(dict(version=version,kind=kind,received=received,
                                    error=f'{type(exc).__name__}: {exc}'))

    def poll(self):
        result=None
        while True:
            try: item=self.ready.get_nowait()
            except queue.Empty: return result
            if item['version']==self.version: result=item

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
