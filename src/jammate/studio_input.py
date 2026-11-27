"""Translate Studio recordings and routing data into the shared InputEvent contract."""
from collections import defaultdict, deque
from math import isfinite
from uuid import uuid4

from jammate.analysis.tempo import tempo_from_onsets
from jammate.contracts import InputEvent
from jammate.contracts.adapters import from_humming, from_rhythm, from_text
from jammate.music import MAX_TEMPO_BPM, MIN_TEMPO_BPM
from jammate.orchestration.agent import _clauses
from jammate.orchestration.meter_intent import METERS
from jammate.rhythm import PAD_DRUM_PITCHES, RhythmContext


STUDIO_TARGETS = {"band", "drums", "keys", "harmony", "bass"}


def with_studio_target(event, body):
    target = body.get("target")
    if target is None:
        return event
    if not isinstance(target, str) or target not in STUDIO_TARGETS:
        raise ValueError("未知的录音素材目标。")
    data = event.to_dict()
    data["provenance"]["target"] = target
    return InputEvent(data)


def studio_text(text, target, *, session_id, source="text"):
    if not isinstance(target, str) or target not in STUDIO_TARGETS:
        raise ValueError("未知的指令目标。")
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 500:
        raise ValueError("请输入 1–500 字的指令和文字素材。")
    original = text.strip()
    scopes = {scope for _, scope, _, _ in _clauses(original)} - {"band"}
    part = {"drums": "鼓", "keys": "键盘", "bass": "贝斯"}.get(target)
    if part and scopes and scopes != {target}:
        raise ValueError("指令提到的声部与已选目标不同。请清除目标或选择对应声部。")
    if target == "harmony" and scopes:
        raise ValueError("指令同时指定了和声和一个声部。请清除目标后明确要修改的对象。")
    routed = part + original if part and not scopes else original
    data = from_text(routed, session_id=session_id, source=source).to_dict()
    data["provenance"].update(target=target, original_text=original)
    return InputEvent(data)


def pad_rhythm(data, *, session_id, beats_per_bar=None):
    """Map one bar of timestamped pad strikes onto the captured tempo grid."""
    hits, bpm, meter = (data.get(key) for key in ("hits", "bpm", "meter"))
    if type(bpm) not in (int, float) or not MIN_TEMPO_BPM <= bpm <= MAX_TEMPO_BPM:
        raise ValueError("鼓垫录入的 BPM 无效。")
    if not isinstance(meter, str) or meter not in METERS:
        raise ValueError("鼓垫录入的拍号无效。")
    beats = METERS[meter]
    if beats_per_bar is not None and beats != beats_per_bar:
        raise ValueError("鼓垫录入的拍号与当前拍号不同，请按当前拍号重新录入。")
    if not isinstance(hits, list) or not 1 <= len(hits) <= 64:
        raise ValueError("请输入 1–64 次鼓垫敲击。")
    count_in = data.get("count_in_ms", [])
    if (not isinstance(count_in, list) or len(count_in) not in (0, 4)
            or any(type(ms) not in (int, float) or not 0 <= ms <= 30_000 for ms in count_in)
            or (count_in and count_in[0] != 0)
            or any(b - a < 150 for a, b in zip(count_in, count_in[1:]))):
        raise ValueError("鼓垫定速需要四下均匀敲击。")
    if count_in:
        estimated = tempo_from_onsets([ms / 1000 for ms in count_in])
        if estimated is None or bpm != int(estimated + .5):
            raise ValueError("鼓垫定速结果与四击记录不一致，请重新录入。")

    bar_ms = beats * 60000 / bpm
    drums = {}
    recorded_hits = []
    previous = 0.
    for hit in hits:
        if not isinstance(hit, dict):
            raise ValueError("鼓垫敲击记录无效。")
        pad, timestamp = hit.get("pad"), hit.get("timestampMs")
        if (type(pad) is not int or not 0 <= pad < 16 or type(timestamp) not in (int, float)
                or not previous <= timestamp <= 300_000):
            raise ValueError("鼓垫编号或起音时间无效。")
        previous = timestamp
        pitch = PAD_DRUM_PITCHES[pad % len(PAD_DRUM_PITCHES)]
        recorded_hits.append({"pad": pad, "pitch": pitch, "timestamp_ms": timestamp})
        if timestamp < bar_ms:
            drums.setdefault(timestamp / bar_ms * beats, set()).add(pitch)
    if not drums:
        raise ValueError("鼓垫录入的首小节没有敲击。")

    context = RhythmContext(bpm, tuple(drums), (1.,) * len(drums), beats_per_bar=beats,
                            drum_pitches=tuple(tuple(sorted(group)) for group in drums.values()))
    event = from_rhythm(context, session_id=session_id, audio_duration_s=max(bar_ms, previous) / 1000,
                        tempo_source="detected" if count_in else "session", confidence_kind="manual").to_dict()
    event["payload"]["pad_recording"] = {"hits": recorded_hits, "count_in_ms": count_in}
    event["provenance"].update(producer="studio_drum_pads", target="drums")
    return InputEvent(event)


def midi_phrase(data, *, session_id):
    """Keep the performance in seconds with velocity and independent note gates."""
    if not isinstance(data, dict):
        raise ValueError("MIDI 素材无效。")
    events = data.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 10000:
        raise ValueError("请提供 1–10000 条 MIDI 音符事件。")
    down = defaultdict(deque)
    notes = []
    cursor = 0
    identity = uuid4().hex
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("MIDI 音符事件无效。")
        kind, pitch, velocity = (event.get(k) for k in ("kind", "note", "velocity"))
        timestamp = event.get("timestampMs")
        channel, track = event.get("channel", 1), event.get("track", 0)
        source = event.get("source", "")
        if (kind not in ("note_on", "note_off") or type(pitch) is not int or not 0 <= pitch <= 127
                or type(velocity) is not int or not 0 <= velocity <= 127
                or type(timestamp) not in (int, float) or not isfinite(timestamp)
                or not cursor <= timestamp <= 300000
                or type(channel) is not int or not 1 <= channel <= 16
                or type(track) is not int or not 0 <= track <= 65535
                or not isinstance(source, str) or len(source) > 256):
            raise ValueError("MIDI 事件的时间、音高、力度或通道无效。")
        cursor = timestamp
        # Channel 10 contains GM percussion. It cannot be rendered as pitched melody.
        if channel == 10:
            continue
        key = (source, track, channel, pitch)
        if kind == "note_on" and velocity:
            down[key].append((timestamp, velocity))
        elif down[key]:
            start, attack = down[key].popleft()
            if timestamp > start:
                note_id = f"n{len(notes) + 1}"
                notes.append(dict(id=note_id, pitchMidi=pitch, startSec=start / 1000,
                                  durationSec=(timestamp - start) / 1000, velocity01=attack / 127,
                                  sourceNoteIds=[note_id]))
    if not notes:
        raise ValueError("没有读到完整的旋律音符。请松开琴键后结束录入，或选择含旋律的 MIDI。")
    notes.sort(key=lambda n: (n["startSec"], n["pitchMidi"]))
    # Hand-performed clips have a recorder-to-first-key delay. Import preserves its written rests.
    lead_in = notes[0]["startSec"] if data.get("origin") == "keyboard" else 0
    for note in notes:
        note["startSec"] -= lead_in
    target = data.get("target", "keys")
    if not isinstance(target, str) or target not in STUDIO_TARGETS:
        raise ValueError("未知的 MIDI 素材目标。")
    source_bpm = data.get("source_bpm")
    if source_bpm is not None and (type(source_bpm) not in (int, float) or not isfinite(source_bpm) or source_bpm <= 0):
        raise ValueError("MIDI 文件速度无效。")
    document = {
        "schemaVersion": "1.0.0", "id": identity, "status": "ready",
        "source": {"id": identity, "durationSec": cursor / 1000 - lead_in, "kind": "midi",
                   "sourceBpm": source_bpm, "trimmedLeadInSec": lead_in},
        "timebase": {"unit": "seconds", "origin": "original_audio_start"},
        "tempo": {"bpm": None, "source": "unknown", "mode": "free", "quantized": False},
        "key": None, "timeSignature": None, "selectedResult": "raw", "notes": notes,
        "results": {"raw": {"notes": notes}, "cleaned": {"notes": notes}},
    }
    data = from_humming(document, session_id=session_id).to_dict()
    data["source"] = "midi"
    data["provenance"].update(producer="studio_midi", target=target)
    return InputEvent(data)
