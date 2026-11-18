"""Input producers used by the workbench, independent of the Agent and renderer."""

import base64
import io

import soundfile as sf

from jammate.analysis.free_rhythm import analyze_free_rhythm
from jammate.contracts import InputEvent
from jammate.contracts.adapters import from_free_rhythm, from_text
from jammate.render.free_rhythm_midi import ENCODING_TEMPO_BPM, free_rhythm_midi


def rhythm_event(body):
    """Analyze an uploaded recording on its own timeline."""
    audio_bytes = base64.b64decode(body["wav"], validate=True)
    with sf.SoundFile(io.BytesIO(audio_bytes)) as source:
        sr = source.samplerate
        duration = source.frames / sr
        if not 8000 <= sr <= 96000 or not .5 <= duration <= 30 or not 1 <= source.channels <= 32:
            raise ValueError("请使用 0.5–30 秒、8–96 kHz、最多 32 声道的录音。")
        audio = source.read(dtype="float32", always_2d=True)
    onsets, accents = analyze_free_rhythm(audio, sr)
    return from_free_rhythm(onsets, accents, audio_duration_s=duration, session_id=body["session_id"],
                            event_id=body.get("event_id"), started_at=body.get("started_at")).to_dict()


def text_event(body):
    return from_text(body["text"], session_id=body["session_id"], event_id=body.get("event_id"),
                     started_at=body.get("started_at"), ended_at=body.get("ended_at")).to_dict()


def rhythm_midi(body):
    event = InputEvent.from_dict(body["event"])
    if event.source != "rhythm" or event.schema_version != "1.3.0":
        raise ValueError("请提供自由节奏的 InputEvent。")
    duration = event.payload["audio_duration_s"]
    midi, hits = free_rhythm_midi(event.payload["rhythm"], duration)
    return {"midi": base64.b64encode(midi).decode("ascii"), "hits": hits,
            "duration_s": duration, "encoding_tempo_bpm": ENCODING_TEMPO_BPM}
