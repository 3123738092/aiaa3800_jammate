"""Execution guarantees shared by the exact and generative plan adapters."""
from jammate.contracts import PlanError


class BackendCapabilityError(PlanError):
    def __init__(self, report):
        self.report = report
        super().__init__("MRT2 cannot guarantee requested preserve/actions; use FluidSynth or explicitly request an uncommitted preview")


def segment_seconds(absolute_beat, performance):
    return (absolute_beat - performance.start_bar * 4) * 60 / performance.tempo_bpm


def capability_report(plan, *, generative=False, silent=False):
    data = plan.to_dict()
    approximate = generative and not silent
    return {"preserve": [{"constraint": value, "guaranteed": not approximate,
                           "evidence": "conditioning_only" if approximate else "symbolic_note_checks"}
                          for value in data["preserve"]],
            "actions": [{"action": action["action"], "scope": action["scope"],
                         "guaranteed": not approximate} for action in data["actions"]],
            "exact_notes": not approximate, "exact_timbre": False,
            "state_commit_allowed": not approximate,
            "limitations": (["25 Hz pitch union; velocity, program and voice identity are lost",
                              "Binary drum condition cannot identify kick/snare/hi-hat",
                              "Generated audio is not a verification of pitch, tempo, key, layers or motif preservation",
                              "Each exported clip starts fresh model context; no physical playback receipt"]
                             if approximate else ["MIDI note fidelity does not prove perceptual quality or physical playback"])}
