from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from jammate.browser_jam import BrowserJam
from jammate.contracts import PlanError
from jammate.contracts.adapters import from_text
from jammate.orchestration.live_text import LiveTextAgent
from jammate.orchestration.llm import validate_plan
from jammate.orchestration.manager import StateManager
from jammate.orchestration.offline import prepare_plan
from jammate.orchestration.plans import make_plan
from jammate.render.synth import SR
from jammate.state import JamState


def tags(operation, target, coverage="exact", constraints="none"):
    return {name: (value, 1.) for name, value in {
        "operation": operation, "target": target, "coverage": coverage,
        "timing": "flexible", "constraints": constraints,
    }.items()}


class SpyPrimary:
    soundfont_programs = {}
    execution_validator = staticmethod(prepare_plan)

    def __init__(self):
        self.calls = []
        self.last_trace = {}

    def decide(self, events, state, *, plan_id, history, intent_hints):
        self.calls.append((events[0].payload["text"], intent_hints))
        self.last_trace = {"source": "llm"}
        return make_plan(state, events, plan_id, "LISTEN", [], ["tempo", "key"], {}, 1, "held", ["test fallback"])


@pytest.mark.parametrize("text, selected, source, expected_action", [
    ("速度设到 92 BPM，让鼓停下来。", {}, "local_explicit", "REMOVE_LAYER"),
    ("换成D大调", {}, "local_explicit", "SET_KEY"),
    ("下一小节静音鼓", {}, "local_explicit", "REMOVE_LAYER"),
    ("把鼓的音量压低一点，贝斯照旧。", tags("quieter", "drums", constraints="preserve"), "jev_direct", "ADJUST_FEEL"),
    ("鼓点可不可以少来几下？贝斯和键盘照现在这样。", tags("thinner", "drums", constraints="preserve"), "jev_direct", "SIMPLIFY"),
    ("鼓放在反拍", tags("drum_offbeat", "drums"), "jev_direct", "SET_TEXTURE"),
    ("加上长笛", tags("add_named", "flute"), "jev_direct", "INTRODUCE_LAYER"),
    ("鼓少一点，江南风情", tags("thinner", "drums"), "llm", None),
    ("江南风情", tags("style", "none", "design"), "llm", None),
    ("挑一件和现在编制互补的乐器", tags("choose_instrument", "none", "design"), "llm", None),
    ("鼓别少一点", tags("thinner", "drums"), "llm", None),
    ("静音鼓，保持节奏", tags("mute", "drums", constraints="preserve"), "llm", None),
    ("把鼓小声一点", tags("quieter", "keys"), "llm", None),
    ("鼓少一点，速度提高 5 BPM", tags("thinner", "drums"), "llm", None),
])
def test_text_routes_are_grounded_in_the_entire_instruction(text, selected, source, expected_action):
    manager = StateManager(JamState(session_id="live-route"))
    events = manager.ingest((from_text(text, session_id="live-route"),))
    primary = SpyPrimary()
    tagger_calls = []

    def tagger(*args):
        tagger_calls.append(args)
        return selected

    agent = LiveTextAgent(primary, tagger)
    plan = agent.decide(events, manager.state)
    assert agent.last_trace["source"] == source
    assert bool(tagger_calls) == (source != "local_explicit")
    if source == "llm":
        assert primary.calls == [(text, selected)]
        assert not plan.actions
    else:
        assert not primary.calls
        assert plan.actions[0]["action"] == expected_action


def test_mixed_style_and_control_cannot_silently_drop_the_style():
    state = JamState(session_id="mixed-style")
    events = (from_text("江南风情，鼓少一点", session_id=state.session_id),)
    action = {"action": "SIMPLIFY", "scope": "drums", "strength": .5, "parameters": {"duration_bars": 4}}
    plan = make_plan(state, events, "partial", "FOLLOW", [action], ["tempo"], {}, 4, "simplify", [])
    with pytest.raises(PlanError, match="style direction"):
        validate_plan(plan, events, state)


class PausedDirector:
    def __init__(self, gate):
        self.gate = gate

    def decide(self, events, state, *, history=()):
        assert self.gate.wait(4)
        if "静音" in events[0].payload["text"]:
            name, scope, parameters = "REMOVE_LAYER", "drums", {}
        else:
            name, scope, parameters = "SET_TEXTURE", "band", {
                "scale": "pentatonic", "drums": "sparse", "bass": "drone", "keys": "open",
            }
        action = {"action": name, "scope": scope, "strength": 1., "parameters": parameters}
        return make_plan(state, events, events[0].event_id, "FOLLOW", [action],
                         ["tempo", "key", "playing"], {}, 4, "test decision", [])


def advance(browser, seconds):
    wanted = round(SR * seconds)
    while wanted >= browser.playing.timing.end_sample:
        bar = browser.playing.timing.bar_index
        edge = browser.playing.timing.end_sample
        browser.poll(bar, edge - 1, bar)
        browser.poll(bar + 1, edge, bar + 1)
    bar = browser.playing.timing.bar_index
    return browser.poll(bar, wanted, bar)


@pytest.mark.parametrize("text, seconds, expected", [
    ("江南风情", 0, "prompt"),
    ("江南风情", 7, "when_ready"),
    ("江南风情", 13, "expired"),
    ("下一小节静音鼓", 0, "prompt"),
    ("下一小节静音鼓", 2, "expired"),
])
def test_same_jam_turn_uses_soft_hard_or_fixed_position(text, seconds, expected):
    permit = Event()
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, lambda: PausedDirector(permit), workers, decision_timeout=12)
        jam.reserve()
        jam.poll(0, 0, 0)
        jam.submit(text)
        future = jam.future
        try:
            waiting = advance(jam, seconds)
            if expected == "when_ready":
                assert waiting["feedback"]["status"] == "waiting_ready"
            assert jam.manager.state.texture.scale == "default"
        finally:
            permit.set()
        if not future.cancelled():
            future.result(timeout=4)
        feedback = jam.snapshot()["feedback"]
        if expected == "expired":
            assert feedback["status"] == "expired"
            assert jam.manager.state.texture.scale == "default" and jam.manager.state.layer("drums").active
            return
        assert feedback["status"] == "scheduled"
        assert feedback["bar"] == (4 if expected == "when_ready" else 1)
        assert feedback["timing"] == expected
        applied = advance(jam, feedback["bar"] * 2.5)
        assert applied["feedback"]["status"] == "applied"
        if "静音" in text:
            assert not jam.manager.state.layer("drums").active
        else:
            assert jam.manager.state.texture.scale == "pentatonic"


def test_new_feedback_cancels_an_older_waiting_plan():
    permit = Event()
    with ThreadPoolExecutor(1) as workers:
        jam = BrowserJam(96, lambda: PausedDirector(permit), workers, decision_timeout=12)
        jam.reserve()
        jam.poll(0, 0, 0)
        jam.submit("江南风情")
        old = jam.future
        try:
            waiting = advance(jam, 6.45)
            assert waiting["feedback"]["status"] == "waiting_ready"
            jam.submit("静音鼓")
        finally:
            permit.set()
        if not old.cancelled():
            old.result(timeout=4)
        jam.future.result(timeout=4)
        new = jam.snapshot()["feedback"]
        assert new["text"] == "静音鼓" and new["status"] == "scheduled"
        advance(jam, new["bar"] * 2.5)
        assert jam.manager.state.texture.scale == "default" and not jam.manager.state.layer("drums").active
