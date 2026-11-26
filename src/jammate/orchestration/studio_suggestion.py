"""Propose an optional next musical move and compile it only after acceptance."""
from math import ceil

from jammate.contracts.band_plan import MAX_PLAN_BARS
from jammate.orchestration.plans import make_plan


def candidate(state):
    if not state.playing or not state.interaction.allow_initiative or state.interaction.user_active:
        return None
    if state.key and state.layer("lead").level > 0:
        return {"kind": "motif", "message": "我先弹一小句，你可以接着它弹。"}
    for motif in reversed(state.motifs):
        if (motif.owner == "user" and motif.notes and state.layer("lead").level > 0
                and max(note.end for note in motif.notes) <= MAX_PLAN_BARS * state.beats_per_bar):
            return {"kind": "echo", "motif": motif.motif_id, "message": "要不要先听我复奏你的旋律，再接一句？"}
    drums = state.layer("drums")
    if drums.active and drums.density > .25 and drums.level > 0:
        return {"kind": "space", "message": "要不要让鼓点松一点，给你留些空间接下一句？"}
    return None


def accepted_plan(offer, events, state):
    plan_id = f"initiative_{offer['id']}"
    keep = ["tempo", "key", "progression", "playing"]
    actions = []
    kind = offer["kind"]
    if kind == "space":
        actions.append({"action": "SIMPLIFY", "scope": "drums", "strength": .5, "parameters": {}})
        bars, reason = 4, "鼓点会在生效小节留出更多空间。"
    else:
        keep += ["groove", *(f"layer:{layer.name}" for layer in state.layers if layer.name != "lead")]
        if not state.layer("lead").active:
            actions.append({"action": "ADD_LAYER", "scope": "lead", "strength": .5, "parameters": {}})
        if kind == "echo":
            motif = state.motif(offer["motif"])
            action, motif_id = "ECHO_MOTIF", motif.motif_id
            bars = max(1, ceil(max(note.end for note in motif.notes) / state.beats_per_bar))
            reason = "从生效小节开始复奏你的旋律。"
        else:
            action, motif_id = "INTRODUCE_MOTIF", f"motif_{offer['id']}"
            bars, reason = 4, "从生效小节开始，我先弹一小句。"
        actions.append({"action": action, "scope": "lead", "strength": .5, "parameters": {"motif_id": motif_id}})
    return make_plan(state, events, plan_id, "INITIATE", actions, keep, {}, bars, reason, [])

# One repeating bar or one short lead phrase. The mixer starts only that part.
STUDIO_INTROS = {
    "bossa_68": {"instrument": "drums", "meter": "6/8", "bpm": 92,
                 "onsets": (0., 2 / 3, 1., 4 / 3), "accents": (1., .46, .72, .52),
                 "message": "试个 6/8 的 Bossa Nova 鼓点？"},
    "offbeat_44": {"instrument": "drums", "meter": "4/4", "bpm": 98,
                   "onsets": (0., 1.5, 2., 3.5), "accents": (1., .52, .74, .43),
                   "message": "试一小节带反拍的鼓点？"},
    "light_34": {"instrument": "drums", "meter": "3/4", "bpm": 88,
                 "onsets": (0., 1., 2.5), "accents": (1., .57, .48),
                 "message": "试一小节三拍子鼓点？"},
    "piano_reply": {"instrument": "lead", "meter": "4/4", "bpm": 88,
                    "message": "我先弹一小句钢琴，你来接下一句？"},
}
