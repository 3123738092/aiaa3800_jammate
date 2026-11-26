"""A playable Bossa Nova response when a rhythm-only session has no harmony."""
import re
from uuid import uuid4

from .jam_opening import text_settings
from .plans import make_plan


def bossa_melody_plan(request):
    state = request.state
    if not state.playing or state.key is not None or len(request.events) != 1:
        return None
    event, = request.events
    if event.event_type != "speech" or not state.layer("drums").active:
        return None
    text = event.payload["text"]
    if (not re.search(r"bossa[ -]+nova|波萨诺瓦", text, re.I)
            or not re.search(r"(加|来|写|弹|创作|生成).{0,45}(旋律|melody)", text, re.I)
            or re.search(r"不要|别加|只要鼓|without|don't", text, re.I)):
        return None
    _, key, _ = text_settings(event)
    plan_id = "bossa_" + uuid4().hex
    actions = [{"action": "SET_KEY", "scope": "band", "strength": 1,
                "parameters": {"tonic": key.tonic if key else 0, "mode": key.mode if key else "major"}}]
    if not state.layer("lead").active:
        actions.append({"action": "ADD_LAYER", "scope": "lead", "strength": 1, "parameters": {}})
    actions.append({"action": "INTRODUCE_MOTIF", "scope": "lead", "strength": 1,
                    "parameters": {"motif_id": "motif_" + plan_id}})
    key_name = key.name if key else "C 大调"
    return make_plan(state, request.events, plan_id, "INITIATE", actions,
                     ["tempo", "playing", "groove", "layer:drums"], {}, 2,
                     f"保留原鼓点，用 {key_name} 加入两小节 Bossa Nova 切分旋律。", [],
                     style="bossa nova")
