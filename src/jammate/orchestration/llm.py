"""LLM-first musical decisions with strict output checks and an explicit hold fallback."""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from importlib.resources import files

from jsonschema import Draft202012Validator

from jammate.contracts import BandPlan, PlanError
from jammate.contracts.input_event import _json_values, _unique_object
from jammate.generate.harmony import harmony_cycle
from jammate.instruments import ADDED_INSTRUMENTS, MAX_JAM_LAYERS, added_program, gm_program
from .plans import make_plan
from .llm_config import LLMConfig, LLMConfigurationError
from .transitions import project
from .context import select_state
from .tempo_intent import explicit_tempo, speech_clauses, tempo_hold, validate_tempo_plan
from .explicit_request import explicit_request, request_hold, validate_request_plan
from .key_intent import explicit_key_plan
from .addition_intent import asks_for_instruments
from .progression_intent import asks_for_new_progression, explicit_progression_plan


class ProviderFailure(RuntimeError):
    """Only a stable error category/status, never a provider body or credential."""
    def __init__(self, kind, status=None):
        super().__init__(kind)
        self.kind, self.status = kind, status


@dataclass(frozen=True)
class ModelReply:
    text: str
    model: str
    usage: dict


def available_instruments(state, soundfont_programs):
    if len(state.layers) >= MAX_JAM_LAYERS:
        return []
    used = {added_program(layer.name) for layer in state.layers}
    from_soundfont = [{"name": f"gm_{program}", "label": label} for program, label in (soundfont_programs or {}).items()
                      if program not in used]
    local = [{"name": name, "label": instrument.label, "role": instrument.role}
             for name, instrument in ADDED_INSTRUMENTS.items()
             if instrument.midi_program not in used and instrument.midi_program not in (soundfont_programs or {})]
    return local + from_soundfont


def decision_schema(state=None, plan_id=None, *, soundfont_programs=None):
    schema = json.loads(files(__package__).joinpath("llm_decision.schema.json").read_text(encoding="utf-8"))
    if state is not None:
        layer_names = [layer.name for layer in state.layers]
        allowed = ["tempo", "key", "progression", "groove", "playing", *(f"layer:{name}" for name in layer_names)]
        if state.current_motif_id:
            allowed.append(f"motif:{state.current_motif_id}")
        schema["properties"]["preserve"]["items"] = {"type": "string", "enum": allowed}
        branches = []
        for branch in schema["properties"]["actions"]["items"]["anyOf"]:
            name = branch["properties"]["action"]["enum"][0]
            params = branch["properties"]["parameters"]["properties"]
            scopes = branch["properties"]["scope"]["enum"]
            if name == "INTRODUCE_LAYER":
                scopes[:] = [item["name"] for item in available_instruments(state, soundfont_programs)]
                if not scopes:
                    continue
            elif name in {"PRESERVE", "BUILD", "SIMPLIFY", "ADJUST_FEEL", "ADD_LAYER", "REMOVE_LAYER"}:
                scopes[:] = [scope for scope in scopes if scope not in ADDED_INSTRUMENTS or scope in layer_names]
                scopes.extend(layer for layer in layer_names if gm_program(layer) is not None)
            if name in {"ECHO_MOTIF", "VARY_MOTIF"}:
                if not state.motifs:
                    continue
                params["motif_id"]["enum"] = [m.motif_id for m in state.motifs]
            elif name == "INTRODUCE_MOTIF":
                params["motif_id"]["enum"] = [f"motif_{plan_id}"]
            elif name == "ADJUST_FEEL":
                params["rhythm_event_id"]["enum"] = [None] + [r.event_id for r in state.rhythm_sources]
            branches.append(branch)
        schema["properties"]["actions"]["items"]["anyOf"] = branches
    return schema


def event_context(event):
    """Select music/text, never dereference/upload local files or opaque provenance."""
    data = event.to_dict() if hasattr(event, "to_dict") else event
    result = {k: data[k] for k in ("event_id", "source", "event_type", "started_at", "ended_at", "confidence")}
    if data["event_type"] == "musical_phrase":
        melody = data["payload"]["melody"]
        result["payload"] = {k: melody[k] for k in ("status", "notes", "tempo", "key", "selectedResult", "timebase")}
        result["payload"]["audio_duration_s"] = melody["source"]["durationSec"]
        result["registered_motif_id"] = f"motif_{data['event_id']}"
    else:
        result["payload"] = data["payload"]
    return result


def build_context(events, state, plan_id, history=(), *, selection=None, soundfont_programs=None):
    if selection is None:
        state, selection = select_state(events, state)
    recent = []
    for turn in history[-6:]:
        recent.append({**turn, "events": [event_context(e) for e in turn["events"]]})
    # Put the CURRENT event last. last_feedback/history are context, not new requests.
    active_names = {layer.name: soundfont_programs[program] for layer in state.layers
                    if (program := gm_program(layer.name)) is not None and program in (soundfont_programs or {})}
    current_chords = [chord.name for chord in harmony_cycle(state)] if state.key is not None else []
    return {"recent_turns": recent, "state": state.to_dict(), "context_selection": selection, "new_motif_id": f"motif_{plan_id}",
            "available_instruments": available_instruments(state, soundfont_programs),
            "soundfont_layer_labels": active_names,
            "current_chord_cycle": current_chords,
            "events": [event_context(e) for e in events]}


class OpenAIProvider:
    def __init__(self, config: LLMConfig, *, client=None, schema_name="jammate_decision_v1"):
        self.config = config
        self.schema_name = schema_name
        if client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise LLMConfigurationError("Install the optional dependency: pip install -e .[llm]") from None
            client = OpenAI(api_key=config.api_key, base_url=config.base_url,
                            timeout=config.timeout_s, max_retries=0)
        self.client = client

    def complete(self, messages, schema, timeout_s):
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        format_spec = {"type": "json_schema", "name": self.schema_name, "strict": True, "schema": schema}
        try:
            client = self.client.with_options(timeout=timeout_s, max_retries=0)
            if self.config.api == "responses":
                options = {"reasoning": {"effort": self.config.reasoning_effort}} if self.config.reasoning_effort is not None else {}
                response = client.responses.create(model=self.config.model, input=messages,
                    text={"format": format_spec}, max_output_tokens=self.config.max_output_tokens, store=False, **options)
                if any(getattr(c, "type", None) == "refusal" for o in response.output for c in getattr(o, "content", ())):
                    raise ProviderFailure("refusal")
                if response.status != "completed":
                    raise ProviderFailure("incomplete_output")
                content = response.output_text
            else:
                options = {"reasoning_effort": self.config.reasoning_effort} if self.config.reasoning_effort is not None else {}
                response = client.chat.completions.create(model=self.config.model, messages=messages,
                    response_format={"type": "json_schema", "json_schema": {k: v for k, v in format_spec.items() if k != "type"}},
                    max_completion_tokens=self.config.max_output_tokens, store=False, **options)
                if not response.choices:
                    raise ProviderFailure("empty_output")
                choice = response.choices[0]
                if choice.message.refusal:
                    raise ProviderFailure("refusal")
                if choice.finish_reason != "stop":
                    raise ProviderFailure("incomplete_output")
                content = choice.message.content
        except APITimeoutError:
            raise ProviderFailure("timeout") from None
        except APIStatusError as exc:
            kind = {401: "authentication", 403: "permission", 429: "rate_limit"}.get(exc.status_code, "http_error")
            raise ProviderFailure(kind, exc.status_code) from None
        except APIConnectionError:
            raise ProviderFailure("connection") from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderFailure("empty_output")
        usage = response.usage.model_dump() if response.usage is not None else {}
        usage = {k: v for k, v in usage.items() if k in {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens"}}
        return ModelReply(content, response.model, usage)


def validate_plan(plan, events, state):
    """Execution invariants, not a second natural-language interpreter."""
    target = project(state, plan)
    data = plan.to_dict()
    has_style_clause = any(
        re.fullmatch(r"[\w\s]{1,24}(?:风情|风格|曲风|氛围)", clause)
        and not re.search(r"不|别|保持|保留|沿用|延续|什么|哪种|分析|介绍|解释",
                          clause.replace("能不能", "").replace("可不可以", ""))
        for event in events for clause in speech_clauses((event,))
    )
    if (has_style_clause and data["transport"].get("playing") is not False
            and not any(a["action"] == "SET_TEXTURE" for a in data["actions"])
            and not any(re.search(r"风情|风格|氛围|style", item, re.I) for item in data["unresolved"])):
        raise PlanError("the request includes a style direction; use SET_TEXTURE or explain that request explicitly")
    if asks_for_new_progression(events):
        actions = {action["action"] for action in data["actions"]}
        if (actions - {"SET_PROGRESSION", "PRESERVE"} or target.key != state.key
                or target.tempo_bpm != state.tempo_bpm or target.playing != state.playing):
            raise PlanError("a progression-only request must keep key, transport and other musical controls")
        if "SET_PROGRESSION" not in actions and (state.key is not None or not data["unresolved"]):
            raise PlanError("SET_PROGRESSION is available; choose a different chord cycle in the current key")
    if asks_for_instruments(events):
        actions = {action["action"] for action in data["actions"]}
        if (actions - {"INTRODUCE_LAYER", "PRESERVE"}
                or target.tempo_bpm != state.tempo_bpm or target.playing != state.playing):
            raise PlanError("an unspecified instrument addition must keep the existing arrangement and transport")
        if "INTRODUCE_LAYER" not in actions and not data["unresolved"]:
            raise PlanError("add a playable instrument or explain why no new instrument was added")
    motifs = [a for a in data["actions"] if a["action"].endswith("MOTIF")]
    if state.interaction.user_active and motifs:
        raise PlanError("user_active: do not issue a lead response while the user is performing")
    idle_only = all(e.event_type == "session_control" and e.payload["command"] == "idle" for e in events)
    if idle_only and state.interaction.awaiting_user and motifs:
        raise PlanError("awaiting_user: an idle-only turn cannot produce another motif")
    can_request_start = any(e.event_type == "speech" or (e.event_type == "session_control" and e.payload["command"] == "start") for e in events)
    if not state.playing and target.playing and not can_request_start:
        raise PlanError("audio/idle alone cannot restart a stopped session")
    if motifs and (not target.playing or target.key is None or not target.layer("lead").active or target.layer("lead").level == 0):
        raise PlanError("motif response needs playing, configured key, and audible lead")
    if motifs and "layer:lead" in data["preserve"]:
        raise PlanError("a new lead utterance cannot preserve the lead track")
    for action in motifs:
        if action["action"] != "INTRODUCE_MOTIF":
            end = max(n.end for n in state.motif(action["parameters"]["motif_id"]).notes)
            if action["action"] == "ECHO_MOTIF" and end > 4 * data["valid_for_bars"]:
                raise PlanError("valid_for_bars is too short for the original motif including rests")
    if "groove" in data["preserve"]:
        before, after = state.layer("drums"), target.layer("drums")
        if (before.active, before.density) != (after.active, after.density):
            raise PlanError("cannot preserve groove while removing/simplifying drums")
    # The renderer ramps one level target per layer. Avoid overlapping writes
    # whose order/duration would otherwise be ambiguous.
    writes = set()
    for action in data["actions"]:
        name, params = action["action"], action["parameters"]
        if name == "ADJUST_FEEL" and not params:
            raise PlanError("ADJUST_FEEL requires level_delta or rhythm_event_id")
        prop = "level" if name == "BUILD" or "level_delta" in params else "density" if name == "SIMPLIFY" else "active" if name in {"INTRODUCE_LAYER", "ADD_LAYER", "REMOVE_LAYER"} else None
        if prop:
            names = tuple(layer.name for layer in target.layers) if action["scope"] == "band" else (action["scope"],)
            for layer in names:
                if (layer, prop) in writes:
                    raise PlanError("consolidate repeated writes to the same layer property")
                writes.add((layer, prop))
    return plan


class LLMAgent:
    def __init__(self, config: LLMConfig, *, provider=None, max_attempts=2, fallback="hold",
                 max_context_chars=100000, execution_validator=None, arrangement_enabled=False,
                 direct_bass_enabled=False, soundfont_programs=None):
        if max_attempts not in (1, 2) or fallback not in {"hold", "rule", "error"}:
            raise ValueError("use one or two attempts and fallback hold/rule/error")
        self.config, self.provider = config, provider or OpenAIProvider(config)
        self.max_attempts, self.fallback, self.max_context_chars = max_attempts, fallback, max_context_chars
        if execution_validator is None:
            from .offline import prepare_plan
            execution_validator = prepare_plan
        self.execution_validator = execution_validator
        self.arrangement_enabled = arrangement_enabled
        self.direct_bass_enabled = direct_bass_enabled
        self.soundfont_programs = dict(soundfont_programs or {})
        self.last_trace = {}

    def _compile(self, content, events, state, plan_id, *, schema=None):
        if not isinstance(content, str) or len(content) > 64000:
            raise PlanError("response must be a bounded JSON object")
        try:
            decision = json.loads(content, object_pairs_hook=_unique_object)
            _json_values(decision)
        except (ValueError, RecursionError):
            raise PlanError("invalid JSON: no markdown fences, duplicate keys or nonfinite numbers") from None
        schema = schema if schema is not None else decision_schema(state, plan_id, soundfont_programs=self.soundfont_programs)
        error = next(Draft202012Validator(schema).iter_errors(decision), None)
        if error:
            if error.validator == "anyOf" and isinstance(error.instance, dict):
                branch = next((b for b in schema["properties"]["actions"]["items"]["anyOf"]
                               if b["properties"]["action"]["enum"][0] == error.instance.get("action")), None)
                if branch:
                    error = next(Draft202012Validator(branch).iter_errors(error.instance), error)
            raise PlanError(f"decision schema violation at {'.'.join(map(str, error.absolute_path))}: {error.message[:240]}")
        actions = [{**a, "parameters": {k: v for k, v in a["parameters"].items() if v is not None}} for a in decision["actions"]]
        plan = make_plan(state, events, plan_id, decision["strategy"], actions, decision["preserve"],
                               {k: v for k, v in decision["transport"].items() if v is not None},
                               decision["valid_for_bars"], decision["reason"], decision["unresolved"])
        for action in actions:
            if action["action"] == "INTRODUCE_MOTIF" and action["parameters"]["motif_id"] != f"motif_{plan_id}":
                raise PlanError("INTRODUCE_MOTIF must use context.new_motif_id")
        if request := explicit_request(events, state=state):
            validate_request_plan(request, state, plan)
        elif intent := explicit_tempo(events):
            validate_tempo_plan(intent, state, plan)
        validate_plan(plan, events, state)
        self.execution_validator(state, plan)
        return plan

    def decide(self, events, state, *, plan_id=None, history=(), intent_hints=None):
        events = tuple(events)
        if not events or any(e.session_id != state.session_id for e in events):
            raise ValueError("Agent needs a nonempty accepted batch in this session")
        plan_id = plan_id or f"plan_{uuid.uuid4().hex}"
        started = time.monotonic()
        self.last_trace = {"plan_id": plan_id, "based_on_state_version": state.state_version,
                           "requested_model": self.config.model, "api": self.config.api,
                           "reasoning_effort": self.config.reasoning_effort,
                           "attempts": [], "source": "llm"}
        # A Runtime stop is an explicit control signal, never delayed by a network request.
        if any(e.event_type == "session_control" and e.payload["command"] == "stop" for e in events):
            self.last_trace.update(source="system_control", elapsed_ms=0.)
            return make_plan(state, events, plan_id, "LISTEN", [], ["tempo", "key"], {"playing": False}, 1,
                                   "Runtime requested stop", [])
        if plan := (explicit_key_plan(events, state, plan_id) or explicit_progression_plan(events, state, plan_id)):
            unresolved = plan.to_dict()["unresolved"]
            if unresolved:
                self.last_trace.update(source="request_gate", status="held", reason=unresolved[0])
            else:
                validate_plan(plan, events, state)
                self.execution_validator(state, plan)
                self.last_trace["source"] = "explicit_music_request"
            self.last_trace["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            return plan
        request = explicit_request(events, state=state)
        tempo_intent = None if request else explicit_tempo(events)
        grounded = request or tempo_intent
        if grounded and (reason := grounded.refusal(state)):
            self.last_trace.update(source="request_gate", elapsed_ms=round((time.monotonic() - started) * 1000, 2),
                                   status="held", reason=reason)
            hold = request_hold if request else tempo_hold
            return hold(events, state, plan_id, reason)
        view, selection = select_state(events, state)
        schema = decision_schema(view, plan_id, soundfont_programs=self.soundfont_programs)
        context_data = build_context(events, view, plan_id, history, selection=selection,
                                     soundfont_programs=self.soundfont_programs)
        if intent_hints:
            context_data = {"classifier_hints": intent_hints, **context_data}
        context = json.dumps(context_data, ensure_ascii=False, allow_nan=False)
        prompt = files(__package__).joinpath("llm_prompt.md").read_text(encoding="utf-8")
        if request:
            prompt += "\n\n" + request.guide(state)
        if self.direct_bass_enabled:
            prompt += (
                "\n\nThe user selected direct bass arrangement. A separate model can write a complete bass part "
                "for a 1–4 bar clip, while the renderer keeps all other parts from the plan. "
                "For an explicit request to write or reshape the bass line, use FOLLOW or RESPOND with one "
                "ADJUST_FEEL action scoped to bass, level_delta=0, and no other actions affecting the bass or band. "
                "Preserve unrequested layers, tempo, key, and groove. Keep the number of bars within 1–4 when "
                "compatible with the request. Do not mark the bass as preserved when authorizing a new bass line. "
                "For an unqualified whole-band opening with INTRODUCE_MOTIF, leave bass available unless "
                "the user asks to preserve it. "
                "The original user request reaches the bass model; explain the delegation, not specific edits. "
                "Keep stop, mute, preserve and ordinary band commands as their usual actions."
            )
        elif self.arrangement_enabled:
            prompt += (
                "\n\nThis session also has an optional note arranger, with access to the current event and final plan. "
                "For explicit local timing, articulation, accent placement or hit-removal requests, use scoped "
                "ADJUST_FEEL on drums, bass or keys and set level_delta to 0 if the overall level should stay the same. "
                "That grants permission to edit the existing onset groups without changing the state-level setting. "
                "For a local edit combined with an overall level change, use the actual signed level_delta in that same action. "
                "Keep tempo, key, motif and unrequested layers preserved. Keep groove when compatible with the request. "
                "Never choose timing or hit-removal edits against a user rhythm anchor, never change lead or invent notes. "
                "Describe the requested local edit as delegated; only the arranger result establishes what changed."
            )
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": context}]
        request_size = lambda: len(json.dumps({"messages": messages, "schema": schema}, ensure_ascii=False, allow_nan=False))
        # Drop whole old turns, never slice the current user input or melody.
        while request_size() > self.max_context_chars and context_data["recent_turns"]:
            context_data["recent_turns"].pop(0)
            selection["older_history_dropped"] += 1
            messages[1]["content"] = json.dumps(context_data, ensure_ascii=False, allow_nan=False)
        self.last_trace.update(context_selection=dict(selection), context_chars=len(messages[1]["content"]),
                               schema_chars=len(json.dumps(schema, ensure_ascii=False)), request_chars=request_size())
        failure = "invalid_output"
        for attempt in range(self.max_attempts):
            # Includes output schema, system prompt and any repair messages.
            if request_size() > self.max_context_chars:
                failure = "context_too_large"
                break
            remaining = self.config.timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                failure = "timeout"
                break
            record = {"attempt": attempt + 1, "request_chars": request_size()}
            self.last_trace["attempts"].append(record)
            try:
                reply = self.provider.complete(messages, schema, remaining)
                record.update(model=reply.model, usage=reply.usage)
                if time.monotonic() - started > self.config.timeout_s:
                    raise ProviderFailure("timeout")
                plan = self._compile(reply.text, events, state, plan_id, schema=schema)
                record["status"] = "accepted"
                self.last_trace["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
                return plan
            except ProviderFailure as exc:
                failure = exc.kind
                record.update(status="provider_failure", kind=exc.kind, http_status=exc.status)
                break  # Authentication, refusal, quota and timeout do not trigger blind retries.
            except (ValueError, StopIteration) as exc:
                failure = "invalid_output"
                record.update(status="invalid_output", validation_error=str(exc)[:240])
                # This is feedback on a model proposal, never a state mutation.
                messages.extend([{"role": "assistant", "content": reply.text[:64000]},
                                 {"role": "user", "content": "The proposal failed validation: " + str(exc)[:500] + ". Return one corrected JSON decision. The original state is unchanged."}])
        self.last_trace.update(source="fallback", failure=failure, elapsed_ms=round((time.monotonic() - started) * 1000, 2))
        if self.fallback == "error":
            raise ProviderFailure(failure)
        if self.fallback == "rule":
            from .agent import RuleAgent
            try:
                plan = RuleAgent().decide(events, state, plan_id=plan_id)
                if request:
                    validate_request_plan(request, state, plan)
                elif tempo_intent:
                    validate_tempo_plan(tempo_intent, state, plan)
                validate_plan(plan, events, state)
                self.execution_validator(state, plan)
                self.last_trace["fallback"] = "rule"
                return plan
            except (ValueError, StopIteration):
                pass
        self.last_trace["fallback"] = "hold"
        return make_plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "playing"], {}, 1,
                               "模型决策暂不可用，保持当前伴奏。", [f"llm_unavailable:{failure}"])
