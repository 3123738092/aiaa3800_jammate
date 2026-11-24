"""Choose a live opening from any available user-supplied music inputs."""

import json
import re
from dataclasses import replace
from math import ceil
from unicodedata import normalize
from uuid import uuid4

from jsonschema import Draft202012Validator, ValidationError

from jammate.analysis.key import estimate_key
from jammate.analysis.tempo import tempo_from_onsets
from jammate.contracts.adapters import to_legacy_phrase
from jammate.contracts.input_event import _unique_object
from jammate.generate.harmony import PROGRESSIONS, progression
from jammate.generate.motif_backing import has_melodic_contour
from jammate.instruments import ADDED_INSTRUMENTS, added_program
from jammate.interaction import Layer
from jammate.music import BEATS_PER_BAR, CHORD_INTERVALS, ChordStep, Key
from jammate.rhythm import RhythmContext
from jammate.state import JamState, MAX_TEMPO_BPM, MIN_TEMPO_BPM
from jammate.texture import ArrangementTexture
from .llm import OpenAIProvider, ProviderFailure
from .key_intent import named_key
from .plans import make_plan
from .progression_intent import named_chords
from .tempo_intent import speech_clauses, tempo_clause


PROMPT = """Choose an opening ensemble, tempo, key, harmony and groove for a live jam.
The user may provide text, claps, humming, or any combination. Respect the
locked fields in the input. Use the text to resolve the other requested musical
parameters. Use humming to inform harmony when present. Choose missing musical
parameters to suit the available material. A chord offset is the number of
semitones above the tonic. The groove lists sorted, distinct sixteenth-note steps
in one 4/4 bar (step 0 = beat 1, step 2 = 1-and, step 12 = beat 4). For an offbeat
pattern, use 2, 6, 10 and 14 unless the user specified other attacks. Do not
invent a replayable humming melody. Lead is available only with real humming
and replays the actual recording's pitches and timing. Choose only instruments
in the catalogue. Core drums, bass and keys are optional. Explain the musical
choices in Chinese, including values chosen for unspecified parameters."""

_KEY = re.compile(r"(?<![a-z])([a-g])\s*([#♯b♭]?)\s*(大调|小调|major\b|minor\b)", re.I)
_BEAT = r"\d+(?:\.\d{1,2})?"
_BEATS = re.compile(rf"第\s*({_BEAT}(?:\s*拍?\s*[、,，和与]\s*第?\s*{_BEAT})*)\s*拍")
_BPM = re.compile(r"(?<![\d.])([0-9]+(?:\.[0-9]+)?)\s*bpm\b", re.I)


def text_settings(event):
    if event is None:
        return None, None, None
    text = normalize("NFKC", event.payload["text"])
    meters = re.findall(r"(?<!\d)(\d+)\s*/\s*(\d+)\s*(?:拍|time\b|(?=[，,。；;]|$))", text, re.I)
    if any(meter != ("4", "4") for meter in meters) or re.search(r"四[二三五六七]拍|八[三五六七]拍", text):
        raise ValueError("当前实时伴奏支持 4/4 拍，请将开场节奏改为 4/4。")
    clauses = speech_clauses((event,))
    candidates = set()
    for clause in clauses:
        intent = tempo_clause(clause)
        if intent is not None:
            if intent.mode == "set":
                candidates.add(float(intent.value))
        elif not re.search(r"不要|不设|不调|别|加快|减慢|升高|降低|\b(?:by|not)\b", clause):
            candidates.update(float(number) for number in _BPM.findall(clause))
    keys = {named_key(*match.groups()) for clause in clauses
            if not re.search(r"不要|不用|别|\b(?:not|without)\b", clause)
            for match in _KEY.finditer(clause)}
    beats = []
    for match in _BEATS.finditer(text):
        if any(mark in text[max(0, match.start() - 6):match.start()] for mark in ("不要", "别", "不打", "休止")):
            continue
        beats.extend(float(value) - 1 for value in re.findall(_BEAT, match.group(1)))
    if beats and any(not 0 <= beat < 4 or not (beat * 4).is_integer() for beat in beats):
        raise ValueError("开场文字拍点需在第 1–4 拍及其十六分音符网格上。")
    if not beats and any(re.search(r"反拍|\boff[- ]?beats?\b", clause)
                         and not re.search(r"不要|不用|别|不打|\b(?:not|without)\b", clause) for clause in clauses):
        beats = [.5, 1.5, 2.5, 3.5]
    return (next(iter(candidates)) if len(candidates) == 1 else None,
            next(iter(keys)) if len(keys) == 1 else None,
            tuple(sorted(set(beats))) or None)


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _cold_opening(events, sources, tempo, tempo_source, key, key_source, beats, claps):
    bpm = tempo if tempo is not None else JamState.tempo_bpm
    if beats:
        rhythm = RhythmContext(bpm, beats, (1.,) * len(beats))
    else:
        rhythm = replace(claps, tempo_bpm=bpm) if claps else None
    clauses = speech_clauses((sources["text"],)) if "text" in sources else ()
    supplied = [chords for clause in clauses if (chords := named_chords(clause))]
    if len(supplied) > 1:
        raise ValueError("请为冷启动明确指定一组和弦进行。")
    steps = ()
    if supplied:
        chords = supplied[0]
        if key is None:
            raise ValueError("冷启动使用和弦进行时，请同时指定调性。")
        if len(set(chords)) < 2:
            raise ValueError("请给出至少两个不同的和弦来组成进行。")
        steps = tuple(ChordStep((chord.root - key.tonic) % 12, chord.quality) for chord in chords)
    labels = {}
    if rhythm is not None:
        labels["drums"] = "鼓组"
    if "humming" in sources:
        labels["lead"] = "主奏 · 默认音色复奏哼唱"
    if steps:
        labels["keys"] = "键盘 · 回放指定和弦"
    base = JamState(tempo_bpm=bpm, session_id=events[0].session_id, auto_harmony=False)
    state = base.with_updates(key=key, rhythm=rhythm, chord_cycle=steps,
                              layers=tuple(replace(layer, active=layer.name in labels) for layer in base.layers))
    return state, {
        "mode": "cold", "model": None,
        "instruments": [{"name": name, "label": label} for name, label in labels.items()],
        "settings": {"tempo_bpm": bpm, "tempo_source": tempo_source,
                     "key": key.name if key else None, "key_source": key_source,
                     "rhythm_source": "text" if beats else "claps" if claps else None,
                     "rhythm_beats_zero_based": list(rhythm.onset_beats) if rhythm else []},
        "reason": ("冷启动使用已提供的音乐素材。哼唱使用一个主奏音色。" if "humming" in sources else
                   "冷启动使用已提供的音乐素材。" if labels else "已设置开场参数，等待拍手或哼唱。"),
    }


def _humming_opening(events, event, text, tempo, tempo_source, key, key_source, beats, claps):
    if event is None:
        raise ValueError("请先录制或上传哼唱，再使用自动补全哼唱。")
    if tempo is None:
        detected = tempo_from_onsets([note["startSec"] for note in event.payload["melody"]["notes"]])
        tempo = detected if detected is not None else JamState.tempo_bpm
        tempo_source = "humming_estimate" if detected is not None else "clock"
    phrase = to_legacy_phrase(event, tempo_bpm=tempo).notes
    if not has_melodic_contour(phrase):
        raise ValueError("请哼一小句有走向的旋律，至少有三个起音和两个不同音高。")
    if key is None:
        estimated = estimate_key(phrase)
        key = estimated.key if estimated else Key(max(phrase, key=lambda note: note.end).pitch % 12)
        key_source = "humming_estimate" if estimated else "humming_tonic"
    supplied = [chords for clause in speech_clauses((text,)) if (chords := named_chords(clause))] if text else []
    if len(supplied) > 1:
        raise ValueError("请为哼唱补全指定一组和弦进行。")
    if supplied:
        chords = supplied[0]
        if len(set(chords)) < 2:
            raise ValueError("请给出至少两个不同的和弦来组成进行。")
    else:
        choices = [progression(key, variant) for variant in range(len(PROGRESSIONS[key.mode]))]
        # Score the time each chord supports the source melody, including notes carried across a bar.
        def fit(cycle):
            return sum(max(0., min(note.end, (bar + 1) * BEATS_PER_BAR) - max(note.start, bar * BEATS_PER_BAR))
                       for note in phrase
                       for bar in range(int(note.start // BEATS_PER_BAR), ceil(note.end / BEATS_PER_BAR))
                       if note.pitch % 12 in cycle[bar % len(cycle)].pcs)
        chords = max(choices, key=fit)
    rhythm = RhythmContext(tempo, beats, (1.,) * len(beats)) if beats else (
        replace(claps, tempo_bpm=tempo) if claps else None)
    labels = {"lead": "主奏 · 哼唱与接续", "keys": "键盘", "bass": "贝斯", "strings": "弦乐"}
    if rhythm is not None:
        labels["drums"] = "鼓组"
    base = JamState(tempo_bpm=tempo, session_id=events[0].session_id)
    layers = tuple(replace(layer, active=layer.name in labels) for layer in base.layers)
    layers += (Layer("strings", level=.32, role="pad"),)
    texture = ArrangementTexture(keys="arpeggio", bass="syncopated") if rhythm is None else ArrangementTexture()
    state = base.with_updates(key=key, rhythm=rhythm,
        chord_cycle=tuple(ChordStep((chord.root - key.tonic) % 12, chord.quality) for chord in chords),
        layers=layers, texture=texture)
    return state, {
        "mode": "humming", "model": None,
        "instruments": [{"name": name, "label": label} for name, label in labels.items()],
        "settings": {"tempo_bpm": tempo, "tempo_source": tempo_source, "key": key.name, "key_source": key_source,
                     "rhythm_source": "text" if beats else "claps" if claps else None,
                     "rhythm_beats_zero_based": list(rhythm.onset_beats) if rhythm else []},
        "reason": "主奏在相近音区接续句尾，键盘、贝斯和轻弦乐继续参考哼唱。",
    }


def cold_audio_plan(request):
    state, events = request.state, request.events
    if state.auto_harmony or not state.playing or any(event.source not in {"rhythm", "humming"} for event in events):
        return None
    actions, bars = [], 1
    for event in events:
        if event.source == "rhythm" and event.payload["rhythm"]["onset_beats"]:
            if not state.layer("drums").active:
                actions.append({"action": "ADD_LAYER", "scope": "drums", "strength": .5, "parameters": {}})
            actions.append({"action": "ADJUST_FEEL", "scope": "band", "strength": .5,
                            "parameters": {"rhythm_event_id": event.event_id}})
        elif event.source == "humming":
            motif_id = f"motif_{event.event_id}"
            motif = next((item for item in state.motifs if item.motif_id == motif_id), None)
            if motif:
                if not state.layer("lead").active:
                    actions.append({"action": "ADD_LAYER", "scope": "lead", "strength": .5, "parameters": {}})
                actions.append({"action": "ECHO_MOTIF", "scope": "lead", "strength": .5,
                                "parameters": {"motif_id": motif_id}})
                bars = max(bars, ceil(max(note.end for note in motif.notes) / 4))
    if not actions:
        return None
    return make_plan(state, events, f"cold_{uuid4().hex}", "RESPOND", actions,
                     ["tempo", "key", "progression", "playing"], {}, bars,
                     "将新素材加入当前 Jam。", [])


def prepare_opening(events, config, programs, *, tempo_override=None, key_override=None, provider=None, mode="warm"):
    by_source = {event.source: event for event in events}
    rhythm_event, humming_event = by_source.get("rhythm"), by_source.get("humming")
    text_event = by_source.get("text")
    text_bpm, text_key, text_beats = text_settings(text_event)
    tempo = tempo_override if tempo_override is not None else text_bpm
    if tempo is not None and not MIN_TEMPO_BPM <= tempo <= MAX_TEMPO_BPM:
        raise ValueError(f"开场速度需在 {MIN_TEMPO_BPM:g}–{MAX_TEMPO_BPM:g} BPM 之间。")
    claps = RhythmContext(**rhythm_event.payload["rhythm"]) if rhythm_event else None
    locked_tempo = tempo if tempo is not None else claps.tempo_bpm if claps else None
    tempo_source = ("manual" if tempo_override is not None else "text" if text_bpm is not None else
                    "claps" if claps else "clock" if mode == "cold" else "model")
    if mode in {"cold", "humming"}:
        key, key_source = text_key, "text" if text_key else None
        if key_override is not None:
            match = _KEY.fullmatch(normalize("NFKC", key_override).strip()) if isinstance(key_override, str) else None
            if match is None:
                raise ValueError("请选择有效的调性，例如 D 大调。")
            key, key_source = named_key(*match.groups()), "manual"
        if mode == "cold":
            return _cold_opening(events, by_source, locked_tempo, tempo_source, key, key_source, text_beats, claps)
        return _humming_opening(events, humming_event, text_event, locked_tempo, tempo_source, key, key_source,
                                text_beats, claps)
    instruments = {"drums": "鼓组", "bass": "贝斯", "keys": "键盘"}
    if humming_event:
        instruments["lead"] = "主奏 · 复奏哼唱"
    instruments.update({name: item.label for name, item in ADDED_INSTRUMENTS.items()})
    instruments.update({f"gm_{program}": name for program, name in programs.items()})
    offset = {"type": "integer", "minimum": 0, "maximum": 11}
    schema = _object({
        "tempo_bpm": {"type": "number", "minimum": MIN_TEMPO_BPM, "maximum": MAX_TEMPO_BPM},
        "rhythm_steps": {"type": "array", "minItems": 1, "maxItems": 16,
                         "items": {"type": "integer", "minimum": 0, "maximum": 15}},
        "tonic": offset,
        "mode": {"type": "string", "enum": ["major", "minor"]},
        "chords": {"type": "array", "minItems": 2, "maxItems": 8, "items": _object({
            "offset": offset, "quality": {"type": "string", "enum": list(CHORD_INTERVALS)},
        })},
        "instruments": {"type": "array", "minItems": 1, "maxItems": 5,
                        "items": {"type": "string", "enum": list(instruments)}},
        "reason": {"type": "string"},
    })
    context = {
        "locked": {"tempo_bpm": locked_tempo,
                   "key": {"tonic": text_key.tonic, "mode": text_key.mode} if text_key else None,
                   "rhythm_steps": [int(beat * 4) for beat in text_beats or (claps.onset_beats if claps else ())]},
        "tempo_bpm": locked_tempo,
        "rhythm": claps.to_dict() if claps else None,
        "humming": [{"pitch": note["pitchMidi"], "start_seconds": note["startSec"],
                     "duration_seconds": note["durationSec"]}
                    for note in humming_event.payload["melody"]["notes"]] if humming_event else [],
        "text": text_event.payload["text"] if text_event else "",
        "instruments": instruments,
    }
    provider = provider or OpenAIProvider(config, schema_name="jammate_live_opening")
    try:
        reply = provider.complete(
            [{"role": "system", "content": PROMPT},
             {"role": "user", "content": json.dumps(context, ensure_ascii=False, allow_nan=False)}],
            schema, config.timeout_s,
        )
    except ProviderFailure as error:
        raise ValueError(f"开场编制模型调用失败（{config.model} · {error.kind}），请重试。") from None
    try:
        data = json.loads(reply.text, object_pairs_hook=_unique_object)
        Draft202012Validator(schema).validate(data)
        if len(set(data["rhythm_steps"])) != len(data["rhythm_steps"]):
            raise ValueError("duplicate rhythm steps")
        selected = data["instruments"]
        if len(set(selected)) != len(selected):
            raise ValueError("duplicate instruments")
        if any(name.startswith("gm_") and added_program(name) == added_program(other)
               for name in selected for other in selected if name != other):
            raise ValueError("duplicate SoundFont program")
    except (ValueError, TypeError, ValidationError, RecursionError):
        raise ValueError(f"开场编制模型返回了无效的选曲结果（{reply.model}），请重试。") from None
    bpm = locked_tempo if locked_tempo is not None else data["tempo_bpm"]
    key = text_key or Key(data["tonic"], data["mode"])
    if claps is not None and text_beats is None:
        rhythm = replace(claps, tempo_bpm=bpm)
        rhythm_source = "claps"
    else:
        beats = text_beats or tuple(step / 4 for step in sorted(data["rhythm_steps"]))
        rhythm = RhythmContext(bpm, beats, (1.0,) * len(beats))
        rhythm_source = "text" if text_beats else "text_interpreted" if text_event else "model"
    base = JamState(tempo_bpm=bpm, session_id=events[0].session_id)
    layers = tuple(Layer(layer.name, active=layer.name in selected, role=layer.role) for layer in base.layers)
    core = {layer.name for layer in layers}
    layers += tuple(Layer(name) for name in selected if name not in core)
    state = base.with_updates(
        rhythm=rhythm, key=key,
        chord_cycle=tuple(ChordStep(**step) for step in data["chords"]), layers=layers,
    )
    reply_text = reply.text.replace(config.api_key, "[redacted]")
    selection = {"mode": "warm", "requested_model": config.model, "model": reply.model, "usage": reply.usage,
                 "reply": reply_text[:8000], "reply_truncated": len(reply_text) > 8000,
                 "instruments": [{"name": name, "label": instruments[name]} for name in selected],
                 "settings": {"tempo_bpm": bpm, "tempo_source": tempo_source,
                              "key": key.name, "key_source": "text" if text_key else "model",
                              "rhythm_source": rhythm_source, "rhythm_beats_zero_based": list(rhythm.onset_beats)},
                 "reason": data["reason"].replace(config.api_key, "[redacted]")[:500]}
    return state, selection
