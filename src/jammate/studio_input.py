"""Translate Studio keyboard and routing data into the shared InputEvent contract."""
from collections import defaultdict, deque
from math import isfinite
from uuid import uuid4

from jammate.contracts import InputEvent
from jammate.contracts.adapters import from_humming, from_text
from jammate.orchestration.agent import _clauses


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
