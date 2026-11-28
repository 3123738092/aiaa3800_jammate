"""Choose a live opening from any available user-supplied music inputs."""

import json
import re
from dataclasses import replace
from math import ceil
from time import monotonic
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
from jammate.music import CHORD_INTERVALS, ChordStep, Key
from jammate.rhythm import RhythmContext
from jammate.state import JamState, MAX_TEMPO_BPM, MIN_TEMPO_BPM
from jammate.texture import ArrangementTexture
from .llm import OpenAIProvider, ProviderFailure
from .key_intent import named_key
from .plans import make_plan
from .progression_intent import named_chords
from .tempo_intent import speech_clauses, tempo_clause


from .meter_intent import METERS, requested_meter


PROMPT = """Choose an opening ensemble, tempo, key, harmony and groove for a live jam.
The user may provide text, claps, humming, or any combination. Respect the
locked fields in the input. Use the text to resolve the other requested musical
parameters. Use humming to inform harmony when present. Choose missing musical
parameters only within the explicitly requested ensemble. Solo and percussion-only
requests must not get unrelated parts. Leave tonic/mode null and chords empty when
harmony is unnecessary. Choose a key for a pitched ensemble that needs accompaniment, even
if the user left the key unspecified. Leave rhythm_steps empty when a rhythm grid is unnecessary.
    A chord offset is the number of
semitones above the tonic. The groove lists sorted, distinct sixteenth-note steps
within the requested meter (step 0 = beat 1). For an offbeat
pattern, use half-beat positions across the requested bar unless the user specified other attacks. Do not
invent a replayable humming melody. Lead is available only with real humming
and replays the actual recording's pitches and timing. Choose only instruments
in the catalogue. Core drums, bass and keys are optional. Explain the musical
choices in Chinese, including values chosen for unspecified parameters."""

_KEY = re.compile(r"(?<![a-z])([a-g])\s*([#♯b♭]?)\s*(大调|小调|major\b|minor\b)", re.I)
_BEAT = r"\d+(?:\.\d{1,2})?"
_BEATS = re.compile(rf"第\s*({_BEAT}(?:\s*拍?\s*[、,，和与]\s*第?\s*{_BEAT})*)\s*拍")
_BPM = re.compile(r"(?<![\d.])([0-9]+(?:\.[0-9]+)?)\s*bpm\b", re.I)
_FULL = re.compile(r"完整[^，。]{0,20}伴奏|全乐队|\b(?:full band|full accompaniment|full arrangement)\b", re.I)
_SOLO = re.compile(r"只(?:要|给我|用)|独奏|\b(?:only|solo|unaccompanied)\b", re.I)
_DEVELOP_PHRASE = re.compile(r"发展|接续|变奏|\b(?:develop|continue|vary)\b", re.I)
_BACKING_REQUEST = re.compile(r"伴奏|和弦|配器|和声|\b(?:backing|accompaniment|chords?|harmony|ensemble)\b", re.I)
_VOICES = (
    (r"鼓点|鼓组|鼓|\b(?:drums?|percussion)\b", "drums", "鼓组", "solo drum groove", None),
    (r"钢琴|键盘|\b(?:piano|keyboard)\b", "keys", "钢琴", "solo piano melody", None),
    (r"电吉他|\belectric guitar\b", "guitar", "电吉他", "solo electric guitar riff", 27),
    (r"尼龙弦吉他|\bnylon(?:-string)? guitar\b", "guitar", "尼龙弦吉他", "solo nylon guitar", 24),
    (r"吉他|\bguitar\b", "guitar", "木吉他", "solo acoustic guitar", None),
    (r"萨克斯|\bsaxophone\b", "lead", "萨克斯", "solo saxophone", 65),
    (r"长笛|\bflute\b", "flute", "长笛", "solo flute", None),
    (r"贝斯|\bbass\b", "bass", "贝斯", "solo bass", None),
)


def opening_focus(event, programs):
    if event is None:
        return None
    text = normalize("NFKC", event.payload["text"])
    if _FULL.search(text):
        return None
    requested = re.split(r"[,，。;；]\s*(?:不要|别加|no\b|without\b)", text, maxsplit=1, flags=re.I)[0]
    matches = [voice for voice in _VOICES if re.search(voice[0], requested, re.I)]
    parts = {voice[1] for voice in matches}
    drum_request = re.fullmatch(
        r"\s*(?:请)?(?:给我(?:来)?(?:个|一段)?|来(?:点|个))?\s*(?:4\s*/\s*4\s*拍?(?:的)?)?\s*(?:鼓点|鼓组|鼓)\s*[。.!]?"
        r"|\s*(?:(?:please )?give me (?:a|some) )?(?:4/4 )?(?:drums?|drum groove|percussion)\s*[.!]?",
        requested, re.I)
    meter_drum_request = parts == {"drums"} and requested_meter(requested) and not re.search(
        r"不要鼓|去掉鼓|别加鼓|保留鼓|旋律|伴奏", requested)
    if (len(parts) == 1 and (drum_request or meter_drum_request or _SOLO.search(requested)
            or re.search(r"旋律|riff|\bmelody\b", requested, re.I))):
        return matches[0]
    if _SOLO.search(requested):
        name = re.sub(r"^(?:请)?只(?:要|给我|用)(?:一段)?\s*|^(?:please )?(?:only|solo)\s+", "", requested, flags=re.I)
        name = re.sub(r"(?:独奏|旋律|\b(?:solo|only|melody)\b)?\s*[。.!]?\s*$", "", name, flags=re.I).strip()
        for program, label in programs.items():
            if name.casefold() == label.casefold():
                return None, "lead", label, f"solo {label}", program
    return None


def text_settings(event):
    if event is None:
        return None, None, None
    text = normalize("NFKC", event.payload["text"])
    # Accept meter text and let the live session carry the requested numerator.
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
    beats_per_bar = text_beats_per_bar(event)
    beats = []
    for match in _BEATS.finditer(text):
        if any(mark in text[max(0, match.start() - 6):match.start()] for mark in ("不要", "别", "不打", "休止")):
            continue
        beats.extend(float(value) - 1 for value in re.findall(_BEAT, match.group(1)))
    if beats and any(beat < 0 or beat >= text_beats_per_bar(event) or not (beat * 4).is_integer() for beat in beats):
        raise ValueError("开场文字拍点需为非负十六分音符网格位置。")
    if not beats and any(re.search(r"反拍|\boff[- ]?beats?\b", clause)
                         and not re.search(r"不要|不用|别|不打|\b(?:not|without)\b", clause) for clause in clauses):
        beats = tuple(beat + .5 for beat in range(beats_per_bar))
    return (next(iter(candidates)) if len(candidates) == 1 else None,
            next(iter(keys)) if len(keys) == 1 else None,
            tuple(sorted(set(beats))) or None)


def text_beats_per_bar(event):
    """Read an explicit numerator from natural-language meter, default to 4."""
    if event is None:
        return 4
    text = normalize("NFKC", event.payload["text"])
    if meter := requested_meter(text):
        return METERS[meter]
    meters = re.findall(r"(?<!\d)(\d+)\s*/\s*(\d+)\s*(?:拍|time\b|(?=[，,。；;]|$))", text, re.I)
    if meters:
        valid = {int(numerator) for numerator, denominator in meters
                 if int(denominator) in (2, 4, 8, 16) and 1 <= int(numerator) <= 16}
        if len(valid) == 1:
            return next(iter(valid))
    named = re.search(r"([一二三四五六七八九十]+)\s*拍子", text)
    if named:
        values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                  "七": 7, "八": 8, "九": 9, "十": 10}
        if named.group(1) in values:
            return values[named.group(1)]
    return 4


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _recorded_rhythm_source(rhythm):
    if rhythm is None:
        return None
    return "drum_pads" if rhythm.drum_pitches else "claps"


def _cold_opening(events, sources, tempo, tempo_source, key, key_source, beats, claps):
    bpm = tempo if tempo is not None else JamState.tempo_bpm
    if beats:
        rhythm = RhythmContext(bpm, beats, (1.,) * len(beats), beats_per_bar=next(
            (source.payload["rhythm"]["beats_per_bar"] for source in events if source.source == "rhythm"),
            text_beats_per_bar(next((source for source in events if source.event_type == "speech"), None))))
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
    if phrase := sources.get("midi", sources.get("humming")):
        if phrase.payload["melody"]["notes"]:
            labels["lead"] = "主奏 · 复奏录入旋律" if phrase.source == "midi" else "主奏 · 默认音色复奏哼唱"
    if steps:
        labels["keys"] = "键盘 · 回放指定和弦"
    bar_length = rhythm.beats_per_bar if rhythm is not None else text_beats_per_bar(next(
        (source for source in events if source.event_type == "speech"), None))
    base = JamState(tempo_bpm=bpm, session_id=events[0].session_id, auto_harmony=False,
                    beats_per_bar=bar_length)
    state = base.with_updates(key=key, rhythm=rhythm, chord_cycle=steps,
                              layers=tuple(replace(layer, active=layer.name in labels) for layer in base.layers))
    return state, {
        "mode": "cold", "model": None,
        "instruments": [{"name": name, "label": label} for name, label in labels.items()],
        "settings": {"tempo_bpm": bpm, "tempo_source": tempo_source,
                     "key": key.name if key else None, "key_source": key_source,
                     "rhythm_source": "text" if beats else _recorded_rhythm_source(claps),
                     "rhythm_beats_zero_based": list(rhythm.onset_beats) if rhythm else []},
        "reason": ("冷启动使用已提供的音乐素材。哼唱使用一个主奏音色。" if "lead" in labels else
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
    bar_length = claps.beats_per_bar if claps is not None and not beats else text_beats_per_bar(
        next((source for source in events if source.event_type == "speech"), None))
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
            return sum(max(0., min(note.end, (bar + 1) * bar_length) - max(note.start, bar * bar_length))
                       for note in phrase
                       for bar in range(int(note.start // bar_length), ceil(note.end / bar_length))
                       if note.pitch % 12 in cycle[bar % len(cycle)].pcs)
        chords = max(choices, key=fit)
    rhythm = RhythmContext(tempo, beats, (1.,) * len(beats), beats_per_bar=bar_length) if beats else (
        replace(claps, tempo_bpm=tempo) if claps else None)
    labels = {"lead": "主奏 · 哼唱与接续", "keys": "键盘", "bass": "贝斯", "strings": "弦乐"}
    if rhythm is not None:
        labels["drums"] = "鼓组"
    base = JamState(tempo_bpm=tempo, session_id=events[0].session_id, beats_per_bar=bar_length)
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
                     "rhythm_source": "text" if beats else _recorded_rhythm_source(claps),
                     "rhythm_beats_zero_based": list(rhythm.onset_beats) if rhythm else []},
        "reason": "主奏在相近音区接续句尾，键盘、贝斯和轻弦乐继续参考哼唱。",
    }


def cold_audio_plan(request):
    state, events = request.state, request.events
    if state.auto_harmony or not state.playing or any(event.source not in {"rhythm", "humming", "midi"} for event in events):
        return None
    actions, bars = [], 1
    for event in events:
        if event.source == "rhythm" and event.payload["rhythm"]["onset_beats"]:
            if not state.layer("drums").active:
                if not state.interaction.allow_initiative and not event.payload["rhythm"].get("drum_pitches"):
                    continue
                actions.append({"action": "ADD_LAYER", "scope": "drums", "strength": .5, "parameters": {}})
            actions.append({"action": "ADJUST_FEEL", "scope": "band", "strength": .5,
                            "parameters": {"rhythm_event_id": event.event_id}})
        elif event.event_type == "musical_phrase":
            motif_id = f"motif_{event.event_id}"
            motif = next((item for item in state.motifs if item.motif_id == motif_id), None)
            if motif and (state.layer("lead").active or state.interaction.allow_initiative):
                if not state.layer("lead").active:
                    actions.append({"action": "ADD_LAYER", "scope": "lead", "strength": .5, "parameters": {}})
                actions.append({"action": "ECHO_MOTIF", "scope": "lead", "strength": .5,
                                "parameters": {"motif_id": motif_id}})
                bars = max(bars, ceil(max(note.end for note in motif.notes) / state.beats_per_bar))
    if not actions:
        return None
    return make_plan(state, events, f"cold_{uuid4().hex}", "RESPOND", actions,
                     ["tempo", "key", "progression", "playing"], {}, bars,
                     "将新素材加入当前 Jam。", [])


def prepare_opening(events, config, programs, *, tempo_override=None, key_override=None, provider=None,
                    mode="warm", renderer="synth", beats_override=None):
    by_source = {event.source: event for event in events}
    rhythm_event, humming_event = by_source.get("rhythm"), by_source.get("midi", by_source.get("humming"))
    text_event = by_source.get("text", by_source.get("speech"))
    if text_event:
        by_source["text"] = text_event
    text_bpm, text_key, text_beats = text_settings(text_event)
    beats_per_bar = beats_override or text_beats_per_bar(text_event)
    tempo = tempo_override if tempo_override is not None else text_bpm
    if tempo is not None and not MIN_TEMPO_BPM <= tempo <= MAX_TEMPO_BPM:
        raise ValueError(f"开场速度需在 {MIN_TEMPO_BPM:g}–{MAX_TEMPO_BPM:g} BPM 之间。")
    claps = RhythmContext(**rhythm_event.payload["rhythm"]) if rhythm_event else None
    input_rhythm = _recorded_rhythm_source(claps)
    locked_tempo = tempo if tempo is not None else claps.tempo_bpm if claps else None
    tempo_source = ("manual" if tempo_override is not None else "text" if text_bpm is not None else
                    input_rhythm or ("clock" if mode == "cold" else "model"))
    focus = opening_focus(text_event, {} if renderer == "mrt2_direct" else programs)
    if renderer == "mrt2_stream" and humming_event and text_event and claps is None and mode == "warm":
        text = text_event.payload["text"]
        if (_DEVELOP_PHRASE.search(text) and not _BACKING_REQUEST.search(text)
                and not _FULL.search(text) and not any(re.search(voice[0], text, re.I) for voice in _VOICES)):
            phrase = to_legacy_phrase(humming_event, tempo_bpm=locked_tempo or JamState.tempo_bpm).notes
            if has_melodic_contour(phrase):
                estimate = estimate_key(phrase) if text_key is None else None
                key = text_key or (estimate.key if estimate else Key(max(phrase, key=lambda n: n.end).pitch % 12))
                source = "text" if text_key else "humming_estimate" if estimate else "humming_tonic"
                state, selection = _cold_opening(events, by_source, locked_tempo,
                                                 "clock" if locked_tempo is None else tempo_source,
                                                 key, source, None, None)
                selection.update(mode=mode, develop_humming=True,
                                 reason="先复奏录入的旋律，再接一句 riff。")
                return state.with_updates(interaction=replace(state.interaction, allow_initiative=False)), selection
    if renderer == "mrt2_direct" or (mode != "humming" and focus):
        state, selection = _cold_opening(events, by_source, locked_tempo,
                                         "clock" if locked_tempo is None else tempo_source,
                                         text_key, "text" if text_key else None, text_beats, claps)
        selection["mode"] = mode
        if renderer == "mrt2_direct" and mode == "humming" and humming_event is None:
            raise ValueError("请先录制或上传哼唱，再使用自动补全哼唱。")
        if renderer == "mrt2_direct" and mode != "cold" and humming_event is not None and not focus:
            selection["develop_humming"] = True
        if key_override is not None:
            match = _KEY.fullmatch(normalize("NFKC", key_override).strip()) if isinstance(key_override, str) else None
            if match is None:
                raise ValueError("请选择有效的调性，例如 D 大调。")
            state = state.with_updates(key=named_key(*match.groups()))
            selection["settings"].update(key=state.key.name, key_source="manual")
        if focus:
            _, layer, label, style, program = focus
            if renderer != "mrt2_direct" and program is not None and program not in programs:
                raise ValueError(f"当前音源没有配置{label}。可选择有相应音色的 FluidSynth 或 MRT2 独立生成。")
            if renderer != "mrt2_direct" and program is not None:
                label = programs[program]
            voice = "drums" if layer == "drums" else "lead"
            rhythm = state.rhythm if voice == "drums" else None
            if voice == "drums" and rhythm is None:
                rhythm = RhythmContext(state.tempo_bpm,
                    tuple(float(beat) for beat in range(beats_per_bar)),
                    tuple(1. if beat == 0 else .65 for beat in range(beats_per_bar)),
                    beats_per_bar=beats_per_bar)
                selection["settings"].update(rhythm_source="requested_groove",
                                             rhythm_beats_zero_based=list(rhythm.onset_beats))
            key = state.key
            if voice == "drums":
                key = None
            elif renderer != "mrt2_direct" and key is None:
                key = Key(0)
                selection["settings"]["key_source"] = "solo_pitch_default"
            state = state.with_updates(key=key, chord_cycle=(), rhythm=rhythm,
                layers=tuple(replace(part, active=part.name == voice) for part in state.layers),
                interaction=replace(state.interaction, allow_initiative=False))
            selection.update(instruments=[{"name": voice, "label": label}], style_hint=style,
                             solo_voice=(f"gm_{program}" if program is not None else layer) if voice == "lead" else None,
                             reason=(f"已请求 MRT2 生成{label}独奏，实际声部以生成结果为准。"
                                     if renderer == "mrt2_direct" else f"开场仅演奏{label}。"))
            selection["settings"]["key"] = key.name if key else None
            if key is None:
                selection["settings"]["key_source"] = None
        return state, selection
    explicit_full = text_event is not None and _FULL.search(text_event.payload["text"])
    if explicit_full and (mode == "cold" or (config is None and provider is None)):
        state, selection = _cold_opening(events, by_source, locked_tempo,
                                         "clock" if locked_tempo is None else tempo_source,
                                         text_key or Key(0), "text" if text_key else "band_default", text_beats, claps)
        labels = {"drums": "鼓组", "bass": "贝斯", "keys": "键盘"}
        if humming_event:
            labels["lead"] = "主奏 · 复奏录入旋律"
        selection.update(mode=mode, instruments=[{"name": name, "label": label} for name, label in labels.items()],
                         reason="根据所要求的完整伴奏，启用鼓、贝斯和键盘。")
        return state.with_updates(auto_harmony=True, layers=tuple(
            replace(layer, active=layer.name in labels) for layer in state.layers)), selection
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
    if text_event is None and humming_event is None and claps is not None:
        state, selection = _cold_opening(events, by_source, locked_tempo, tempo_source, None, None, None, claps)
        state = state.with_updates(interaction=replace(state.interaction, allow_initiative=False))
        return state, {**selection, "mode": "warm", "reason": "沿用录入的节奏，保持纯鼓开场。"}
    if text_event is None and humming_event is not None and claps is None:
        state, selection = _humming_opening(events, humming_event, None, locked_tempo, tempo_source,
                                             None, None, None, None)
        return state, {**selection, "mode": "warm", "develop_humming": True}
    if config is None:
        raise ValueError("此暖启动需要可用的开场模型。可改用冷启动或明确指定声部。")
    instruments = {"drums": "鼓组", "bass": "贝斯", "keys": "键盘"}
    if humming_event:
        instruments["lead"] = "主奏 · 复奏哼唱"
    instruments.update({name: item.label for name, item in ADDED_INSTRUMENTS.items()})
    instruments.update({f"gm_{program}": name for program, name in programs.items()})
    offset = {"type": "integer", "minimum": 0, "maximum": 11}
    schema = _object({
        "tempo_bpm": {"type": "number", "minimum": MIN_TEMPO_BPM, "maximum": MAX_TEMPO_BPM},
        "rhythm_steps": {"type": "array", "minItems": 0, "maxItems": 64,
                         "items": {"type": "integer", "minimum": 0, "maximum": beats_per_bar * 4 - 1}},
        "tonic": {"anyOf": [offset, {"type": "null"}]},
        "mode": {"enum": ["major", "minor", None]},
        "chords": {"type": "array", "minItems": 0, "maxItems": 8, "items": _object({
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
        "beats_per_bar": beats_per_bar,
        "rhythm": claps.to_dict() if claps else None,
        "humming": [{"pitch": note["pitchMidi"], "start_seconds": note["startSec"],
                     "duration_seconds": note["durationSec"]}
                    for note in humming_event.payload["melody"]["notes"]] if humming_event else [],
        "text": text_event.payload["text"] if text_event else "",
        "target": text_event.to_dict().get("provenance", {}).get("target") if text_event else None,
        "instruments": instruments,
    }
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, allow_nan=False)}]
    provider = provider or OpenAIProvider(config, schema_name="jammate_live_opening")
    deadline = monotonic() + config.timeout_s
    for attempt in range(2):
        try:
            reply = provider.complete(messages, schema, max(.01, deadline - monotonic()))
        except ProviderFailure as error:
            raise ValueError(f"开场编制模型调用失败（{config.model} · {error.kind}），请重试。") from None
        try:
            data = opening_choice(reply.text, schema, text_key)
        except (ValueError, TypeError, ValidationError, RecursionError) as error:
            if attempt or deadline - monotonic() < 1:
                raise ValueError(f"开场编制模型返回了无效的选曲结果（{reply.model}），请重试。") from None
            detail = error.message if isinstance(error, ValidationError) else str(error)
            messages.append({"role": "user", "content": "Return corrected JSON matching the schema and input. "
                             "The previous answer was invalid: " + detail.replace(config.api_key, "[redacted]")[:240]})
        else:
            break
    selected = data["instruments"]
    bpm = locked_tempo if locked_tempo is not None else data["tempo_bpm"]
    key = text_key or (Key(data["tonic"], data["mode"]) if data["tonic"] is not None else None)
    if claps is not None and text_beats is None:
        rhythm = replace(claps, tempo_bpm=bpm)
        rhythm_source = input_rhythm
    else:
        beats = text_beats or tuple(step / 4 for step in sorted(data["rhythm_steps"]))
        rhythm = RhythmContext(bpm, beats, (1.0,) * len(beats), beats_per_bar=beats_per_bar) if beats else None
        rhythm_source = ("text" if text_beats else "text_interpreted" if text_event else "model") if rhythm else None
    base = JamState(tempo_bpm=bpm, session_id=events[0].session_id)
    layers = tuple(Layer(layer.name, active=layer.name in selected, role=layer.role) for layer in base.layers)
    core = {layer.name for layer in layers}
    layers += tuple(Layer(name) for name in selected if name not in core)
    bar_length = rhythm.beats_per_bar if rhythm is not None else text_beats_per_bar(next(
        (source for source in events if source.event_type == "speech"), None))
    state = base.with_updates(
        rhythm=rhythm, key=key, beats_per_bar=bar_length,
        chord_cycle=tuple(ChordStep(**step) for step in data["chords"]), layers=layers,
        auto_harmony=key is not None and any(name not in {"drums", "lead"} for name in selected),
        interaction=replace(base.interaction, allow_initiative=len(selected) > 1),
    )
    reply_text = reply.text.replace(config.api_key, "[redacted]")
    selection = {"mode": "warm", "requested_model": config.model, "model": reply.model, "usage": reply.usage,
                 "reply": reply_text[:8000], "reply_truncated": len(reply_text) > 8000,
                 "instruments": [{"name": name, "label": instruments[name]} for name in selected],
                 "settings": {"tempo_bpm": bpm, "tempo_source": tempo_source,
                              "key": key.name if key else None, "key_source": "text" if text_key else "model" if key else None,
                              "rhythm_source": rhythm_source,
                              "rhythm_beats_zero_based": list(rhythm.onset_beats) if rhythm else []},
                 "reason": data["reason"].replace(config.api_key, "[redacted]")[:500]}
    return state, selection


def opening_choice(text, schema, text_key):
    data = json.loads(text, object_pairs_hook=_unique_object)
    Draft202012Validator(schema).validate(data)
    if len(set(data["rhythm_steps"])) != len(data["rhythm_steps"]):
        raise ValueError("duplicate rhythm steps")
    if (data["tonic"] is None) != (data["mode"] is None):
        raise ValueError("tonic and mode must either both describe the key or both be null")
    if len(data["chords"]) == 1 or (data["chords"] and text_key is None and data["tonic"] is None):
        raise ValueError("provide a key and at least two chords, or leave the chord list empty")
    selected = data["instruments"]
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate instruments")
    if any(name.startswith("gm_") and added_program(name) == added_program(other)
           for name in selected for other in selected if name != other):
        raise ValueError("duplicate SoundFont program")
    return data
