"""Ask a model to choose local edits supported by humming transcription evidence."""

import json
from time import monotonic

from jammate.orchestration.llm import ProviderFailure
from jammate.orchestration.llm_config import LLMConfigurationError

TIMEOUT_S = 8.
MAX_EDITS = 3
MAX_NOTES = 96
PROMPT = """You are correcting transcription errors in a single hummed melody.
Review the whole phrase and choose only justified edits from candidates. Prefer no edit if unsure.
Merge an unarticulated pitch drift inside a sustained note. Retune a weak drift only if
the alternate pitch fits the preceding or following stable note and the acoustic evidence.
Drop an unsupported pitch outlier only if it is likely noise. A longer event offered for review
has low pitch activation, no acoustic re-attack and little same-pitch evidence in the raw
decoder. Duration alone does not make it reliable. Keep a quiet intentional note if it fits
the phrase or if the evidence is ambiguous. The pitch model can report
an onset even during a continuous glide. Consider acoustic_attack alongside the onset score.
Preserve intentional chromatic notes, leaps, repeated notes and rests. Activations are model
evidence, not probabilities. Return short reasons in Chinese. Output an empty edits array if the phrase
sounds intentional or the evidence is insufficient."""


def _schema(candidate_ids):
    return {
        "type": "object", "additionalProperties": False, "required": ["edits"],
        "properties": {"edits": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["candidate_id", "reason"],
            "properties": {"candidate_id": {"type": "string", "enum": candidate_ids}, "reason": {"type": "string"}},
        }}},
    }


def _end(note):
    return note["startSec"] + note["durationSec"]


def _available_edits(notes, raw, attacks):
    edits = {}

    def offer(action, targets, pitch=None, *, evidence=None):
        edit = {"action": action, "note_ids": [note["id"] for note in targets]}
        if pitch is not None:
            edit["pitch_midi"] = pitch
        if evidence is not None:
            edit["evidence"] = evidence
        edits[f"c{len(edits) + 1}"] = edit

    for index, note in enumerate(notes):
        onset = note.get("onsetActivation")
        attack = attacks.get(note["id"])
        if onset is None or attack is True:
            continue
        pitch = note["pitchMidi"]
        duration = note["durationSec"]
        activation = note.get("modelActivation")
        if duration > .26:
            if (len(notes) >= 4 and duration <= 1.25 and attack is False and onset < .30 and
                    activation is not None and activation < .22):
                matching = sum(max(0., min(_end(candidate), _end(note)) -
                                   max(candidate["startSec"], note["startSec"]))
                               for candidate in raw if candidate["pitchMidi"] == pitch)
                if matching / duration < .25:
                    offer("drop", [note], evidence={"same_pitch_raw_fraction": round(matching / duration, 3)})
            continue
        previous = notes[index - 1] if index else None
        following = notes[index + 1] if index + 1 < len(notes) else None
        if previous and following:
            outer_pitch = previous["pitchMidi"]
            continuous = (abs(note["startSec"] - _end(previous)) <= .025 and
                          abs(following["startSec"] - _end(note)) <= .025)
            following_onset = following.get("onsetActivation")
            following_attack = attacks.get(following["id"])
            weak_onsets = onset < .45 and following_onset is not None and following_onset < .65
            sustained_return = (attack is False and following_attack is False and
                                1.5 * note["durationSec"] <= min(previous["durationSec"], following["durationSec"]))
            if (continuous and outer_pitch == following["pitchMidi"] and
                    0 < abs(outer_pitch - pitch) <= 2 and following_attack is not True and
                    (weak_onsets or sustained_return)):
                offer("merge", [previous, note, following], outer_pitch)
                continue
        if onset >= .45:
            continue

        if previous and following:
            if (note["durationSec"] <= .20 and activation is not None and activation < .50 and
                    min(abs(pitch - previous["pitchMidi"]), abs(pitch - following["pitchMidi"])) >= 7):
                offer("drop", [note])
                continue

        neighbours = {item["pitchMidi"] for item in (previous, following) if item and
                      item["durationSec"] >= .24 and abs(item["pitchMidi"] - pitch) == 1 and
                      item["startSec"] < _end(note) + .04 and _end(item) > note["startSec"] - .04}
        for other_pitch in sorted(neighbours):
            if any(candidate["pitchMidi"] == other_pitch and
                   min(_end(candidate), _end(note)) - max(candidate["startSec"], note["startSec"])
                   >= .5 * note["durationSec"] for candidate in raw):
                offer("retune", [note], other_pitch)
    return edits


def _read_edits(text, candidates):
    reply = json.loads(text)
    if not isinstance(reply, dict) or set(reply) != {"edits"}:
        raise ValueError("expected edits")
    edits = reply["edits"]
    if not isinstance(edits, list) or len(edits) > MAX_EDITS:
        raise ValueError("too many edits")
    selected, used = [], set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"candidate_id", "reason"}:
            raise ValueError("invalid edit fields")
        identity, reason = edit["candidate_id"], edit["reason"]
        if not isinstance(identity, str) or identity not in candidates or not isinstance(reason, str) or not reason.strip():
            raise ValueError("unknown candidate or empty reason")
        candidate = candidates[identity]
        if used.intersection(candidate["note_ids"]):
            raise ValueError("overlapping edits")
        used.update(candidate["note_ids"])
        selected.append({"candidate_id": identity, **candidate, "reason": reason.strip()[:240]})
    return selected


def _apply_edits(notes, edits):
    by_id = {note["id"]: note for note in notes}
    replaced = {}
    for edit in edits:
        first, *rest = edit["note_ids"]
        if edit["action"] == "drop":
            replaced[first] = None
            continue
        note = {**by_id[first], "pitchMidi": edit["pitch_midi"], "modelActivation": None}
        if edit["action"] == "merge":
            tail = by_id[rest[-1]]
            note["durationSec"] = round(_end(tail) - note["startSec"], 6)
            note["frameRange"] = [note["frameRange"][0], tail["frameRange"][1]]
            note["sourceNoteIds"] = list(dict.fromkeys(
                source for identity in edit["note_ids"] for source in by_id[identity]["sourceNoteIds"]
            ))
            replaced.update(dict.fromkeys(rest))
        replaced[first] = note
    return [result for note in notes if (result := replaced.get(note["id"], note)) is not None]


def correct_humming(document, complete):
    cleaned = document["results"]["cleaned"]
    notes = cleaned["notes"]
    report = {"status": "skipped", "before_notes": notes, "edits": []}

    def result(output):
        return {**document, "notes": output, "llmCorrection": report,
                "results": {**document["results"], "cleaned": {**cleaned, "notes": output}}}

    if cleaned.get("forceMonophonic") is not True or not all("frameRange" in note for note in notes):
        report["reason"] = "missing_frame_evidence"
        return result(notes)
    if len(notes) > MAX_NOTES:
        report["reason"] = "phrase_too_dense"
        return result(notes)
    evidence = cleaned.get("acousticAttacks")
    attacks = evidence if isinstance(evidence, dict) else {}
    candidates = _available_edits(notes, document["results"]["raw"]["notes"], attacks)
    if not candidates:
        report["reason"] = "no_candidates"
        return result(notes)
    report["candidates"] = candidates

    context = {
        "max_edits": MAX_EDITS,
        "notes": [{**{field: note.get(field) for field in (
            "id", "pitchMidi", "startSec", "durationSec", "modelActivation", "onsetActivation"
        )}, "acoustic_attack": attacks.get(note["id"])} for note in notes],
        "candidates": candidates,
    }
    started = monotonic()
    try:
        reply = complete([{"role": "system", "content": PROMPT},
                          {"role": "user", "content": json.dumps(context, allow_nan=False)}],
                         _schema(list(candidates)), TIMEOUT_S)
        report["model"] = reply.model
        report["usage"] = reply.usage
        edits = _read_edits(reply.text, candidates)
    except LLMConfigurationError:
        report.update(status="fallback", reason="model_unconfigured")
    except ProviderFailure as error:
        report.update(status="fallback", reason=error.kind)
    except (TypeError, ValueError):
        report.update(status="fallback", reason="invalid_reply")
    else:
        corrected = _apply_edits(notes, edits)
        if not corrected:
            report.update(status="fallback", reason="empty_phrase")
            return result(notes)
        report.update(status="applied" if edits else "kept", edits=edits)
        return result(corrected)
    finally:
        report["elapsed_ms"] = round(1000 * (monotonic() - started))
    return result(notes)
