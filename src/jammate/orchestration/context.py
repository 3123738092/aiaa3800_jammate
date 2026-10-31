"""Bounded LLM read view; the authoritative state and input stay lossless."""
import re


def select_state(events, state, *, motif_limit=12, rhythm_limit=8):
    text = "\n".join(e.payload["text"] for e in events if e.event_type == "speech")
    mentioned = lambda identifier: bool(re.search(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", text))
    motifs = {f"motif_{e.event_id}" for e in events if e.event_type == "musical_phrase"}
    rhythms = {e.event_id for e in events if e.event_type == "rhythm_seed"}
    if state.current_motif_id:
        motifs.add(state.current_motif_id)
    # Current input and explicit references take priority over recency. A large
    # current batch may exceed the target counts; the request budget then guards it.
    motifs.update(m.motif_id for m in state.motifs if mentioned(m.motif_id))
    rhythms.update(r.event_id for r in state.rhythm_sources if mentioned(r.event_id))
    if state.rhythm is not None:
        match = next((r for r in reversed(state.rhythm_sources) if r.context == state.rhythm), None)
        if match:
            rhythms.add(match.event_id)

    def choose(items, pinned, limit, key):
        selected = {key(item) for item in items if key(item) in pinned}
        for item in reversed(items):
            if len(selected) >= limit:
                break
            selected.add(key(item))
        return tuple(item for item in items if key(item) in selected)

    selected_motifs = choose(state.motifs, motifs, motif_limit, lambda m: m.motif_id)
    selected_rhythms = choose(state.rhythm_sources, rhythms, rhythm_limit, lambda r: r.event_id)
    view = state.with_updates(motifs=selected_motifs, rhythm_sources=selected_rhythms)
    info = {"mode": "bounded_read_view", "motifs_total": len(state.motifs), "motifs_visible": len(selected_motifs),
            "motifs_omitted": len(state.motifs) - len(selected_motifs), "rhythms_total": len(state.rhythm_sources),
            "rhythms_visible": len(selected_rhythms), "rhythms_omitted": len(state.rhythm_sources) - len(selected_rhythms),
            "older_history_dropped": 0,
            "retrieval": "Current/active, explicitly named IDs, then recent material. Omitted material remains in JamState; ask for an ID/reselection when an old reference is unavailable."}
    return view, info
