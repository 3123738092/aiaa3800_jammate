"""LLM-first musical decisions with strict output checks and an explicit hold fallback."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from importlib.resources import files

from jsonschema import Draft202012Validator

from jammate.contracts import BandPlan, PlanError
from jammate.contracts.input_event import _json_values, _unique_object
from .plans import make_plan
from .llm_config import LLMConfig, LLMConfigurationError
from .transitions import project
from .context import select_state


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


def decision_schema(state=None, plan_id=None):
    schema = json.loads(files(__package__).joinpath("llm_decision.schema.json").read_text(encoding="utf-8"))
    schema["properties"]["preserve"]["items"]["pattern"] = "^(tempo|key|groove|playing|motif:.+|layer:(drums|bass|keys|lead))$"
    if state is not None:
        allowed = ["tempo", "key", "groove", "playing", "layer:drums", "layer:bass", "layer:keys", "layer:lead"]
        if state.current_motif_id:
            allowed.append(f"motif:{state.current_motif_id}")
        schema["properties"]["preserve"]["items"] = {"type": "string", "enum": allowed}
        branches = []
        for branch in schema["properties"]["actions"]["items"]["anyOf"]:
            name = branch["properties"]["action"]["enum"][0]
            params = branch["properties"]["parameters"]["properties"]
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


def build_context(events, state, plan_id, history=(), *, selection=None):
    if selection is None:
        state, selection = select_state(events, state)
    recent = []
    for turn in history[-6:]:
        recent.append({**turn, "events": [event_context(e) for e in turn["events"]]})
    # Put the CURRENT event last. last_feedback/history are context, not new requests.
    return {"recent_turns": recent, "state": state.to_dict(), "context_selection": selection, "new_motif_id": f"motif_{plan_id}",
            "events": [event_context(e) for e in events]}


class OpenAIProvider:
    def __init__(self, config: LLMConfig, *, client=None):
        self.config = config
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

        format_spec = {"type": "json_schema", "name": "jammate_decision_v1", "strict": True, "schema": schema}
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
        prop = "level" if name == "BUILD" or "level_delta" in params else "density" if name == "SIMPLIFY" else "active" if name in {"ADD_LAYER", "REMOVE_LAYER"} else None
        if prop:
            names = ("drums", "bass", "keys", "lead") if action["scope"] == "band" else (action["scope"],)
            for layer in names:
                if (layer, prop) in writes:
                    raise PlanError("consolidate repeated writes to the same layer property")
                writes.add((layer, prop))
    return plan


class LLMAgent:
    def __init__(self, config: LLMConfig, *, provider=None, max_attempts=2, fallback="hold", max_context_chars=100000, execution_validator=None):
        if max_attempts not in (1, 2) or fallback not in {"hold", "rule", "error"}:
            raise ValueError("use one or two attempts and fallback hold/rule/error")
        self.config, self.provider = config, provider or OpenAIProvider(config)
        self.max_attempts, self.fallback, self.max_context_chars = max_attempts, fallback, max_context_chars
        if execution_validator is None:
            from .offline import prepare_plan
            execution_validator = prepare_plan
        self.execution_validator = execution_validator
        self.last_trace = {}

    def _compile(self, content, events, state, plan_id, *, schema=None):
        if not isinstance(content, str) or len(content) > 64000:
            raise PlanError("response must be a bounded JSON object")
        try:
            decision = json.loads(content, object_pairs_hook=_unique_object)
            _json_values(decision)
        except (ValueError, RecursionError):
            raise PlanError("invalid JSON: no markdown fences, duplicate keys or nonfinite numbers") from None
        schema = schema if schema is not None else decision_schema(state, plan_id)
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
        validate_plan(plan, events, state)
        self.execution_validator(state, plan)
        return plan

    def decide(self, events, state, *, plan_id=None, history=()):
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
        view, selection = select_state(events, state)
        schema = decision_schema(view, plan_id)
        context_data = build_context(events, view, plan_id, history, selection=selection)
        context = json.dumps(context_data, ensure_ascii=False, allow_nan=False)
        messages = [{"role": "system", "content": files(__package__).joinpath("llm_prompt.md").read_text(encoding="utf-8")},
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
                validate_plan(plan, events, state)
                self.execution_validator(state, plan)
                self.last_trace["fallback"] = "rule"
                return plan
            except (ValueError, StopIteration):
                pass
        self.last_trace["fallback"] = "hold"
        return make_plan(state, events, plan_id, "LISTEN", [], ["tempo", "key", "playing"], {}, 1,
                               "模型决策暂不可用，保持当前伴奏。", [f"llm_unavailable:{failure}"])
