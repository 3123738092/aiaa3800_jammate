"""Isolated check of the pinned Agent with scripted provider replies."""
import base64
from contextlib import redirect_stdout
from functools import partial
import io
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pretty_midi

from jammate.orchestration.llm import LLMAgent, ModelReply, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from scripts import reference_worker


class Provider:
    calls = 0

    def complete(self, messages, schema, timeout_s):
        self.calls += 1
        context = json.loads(messages[-1]["content"])
        event, = context["events"]
        if self.calls == 1:
            assert event["payload"]["text"] == "写一小段，让我能在后面接着唱。"
            assert not context["recent_turns"]
            strategy, action, scope = "INITIATE", "INTRODUCE_MOTIF", "lead"
            parameters = {"motif_id": context["new_motif_id"]}
        else:
            assert event["payload"]["text"] == "后面别让贝斯盖住人声，直接把它拿掉。"
            assert any(plan["status"] == "applied" for turn in context["recent_turns"] for plan in turn["plans"])
            if self.calls == 2:
                raise ProviderFailure("timeout")
            strategy, action, scope = "FOLLOW", "REMOVE_LAYER", "bass"
            parameters = {}
        result = {"strategy": strategy, "valid_for_bars": 4, "preserve": ["tempo", "key"],
                  "actions": [{"action": action, "scope": scope, "strength": .5, "parameters": parameters}],
                  "transport": {"playing": None, "tempo_bpm": None}, "reason": "fixture", "unresolved": []}
        return ModelReply(json.dumps(result), "scripted-provider", {})


def midi(result):
    return pretty_midi.PrettyMIDI(io.BytesIO(base64.b64decode(result["midi"])))


def main():
    provider = Provider()
    reference_worker.LLMAgent = partial(LLMAgent, provider=provider)
    service = reference_worker.ReferenceService(None, None)
    service.config = lambda: LLMConfig(api_key="local-test-fixture")
    base = {"session_id": "probe", "bpm": 100, "agent": "llm", "profile": "electric"}
    first = service.text_turn({**base, "after_turn": 0, "request_id": "first",
                               "text": "写一小段，让我能在后面接着唱。"})
    assert first["agent"] == "llm"
    assert any(part.name == "lead" for part in midi(first).instruments)
    edit = {**base, "after_turn": 1, "request_id": "edit", "text": "后面别让贝斯盖住人声，直接把它拿掉。"}
    try:
        service.text_turn(edit)
    except ProviderFailure:
        pass
    else:
        raise AssertionError("the fixture must surface its first failure")
    second = service.text_turn(edit)
    assert second["turn"] == 2 and second["start_bar"] == first["bars"]
    assert all(part.name != "bass" for part in midi(second).instruments)
    assert service.text_turn(edit) == second and provider.calls == 3


def check_applied_turn_media_retry():
    service = reference_worker.ReferenceService(None, None)
    original_media = reference_worker.media

    def interrupt_once(*args):
        reference_worker.media = original_media
        raise OSError("temporary output read error")

    reference_worker.media = interrupt_once
    request = {"session_id": "recover-media", "agent": "rule", "profile": "electric", "bpm": 100,
               "after_turn": 0, "request_id": "first", "text": "来一段，慢一点"}
    try:
        service.text_turn(request)
    except (OSError, ValueError):
        pass
    else:
        raise AssertionError("the fixture must interrupt packaging")
    recovered = service.text_turn(request)
    assert recovered["turn"] == 1 and recovered["start_bar"] == 0 and recovered["tempo_bpm"] == 92
    assert service.text_turn(request) == recovered


def check_render_timeout_keeps_the_worker_available():
    requests = "\n".join(json.dumps({"operation": operation, "body": {}})
                         for operation in ("text/turn", "text/status")) + "\n"
    stdout = io.StringIO()
    snapshot = Path(reference_worker.jammate.__file__).resolve().parents[2]
    with patch("sys.stdin", io.StringIO(requests)), redirect_stdout(stdout), patch.object(
        reference_worker.ReferenceService, "text_turn", side_effect=subprocess.TimeoutExpired("fluidsynth", 180)
    ):
        reference_worker.main(snapshot, "", "")
    failed, next_reply = map(json.loads, stdout.getvalue().splitlines())
    assert "error" in failed
    assert "llm_ready" in next_reply["result"]


if __name__ == "__main__":
    main()
    check_applied_turn_media_retry()
    check_render_timeout_keeps_the_worker_available()
