"""Run matched held-out text-service sessions with direct Mini and Jev/Mini."""
import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from math import isclose
import os
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter
import wave

import pretty_midi

from classifier_round2 import state_summary as base_state_summary
from classifier_round3 import jev_slots
from jammate.orchestration.jev_hybrid import JevHybridAgent
from jammate.orchestration.llm import LLMAgent, ProviderFailure
from jammate.orchestration.llm_config import LLMConfig
from jammate.state import JamState
from scripts.reference_worker import ReferenceService


MODES = ("mini", "hybrid")
FIXTURE = Path(__file__).with_name("classifier_round5_cases.json")


class MeasuredService(ReferenceService):
    def __init__(self, mode, config, jev_key):
        super().__init__(None, None)
        self.mode = mode
        self.key = jev_key
        self.cfg = config
        self.agent = None
        self.injection = None

    def config(self):
        return self.cfg

    def make_llm_agent(self, config, **options):
        agent = LLMAgent(config, **options)
        if self.mode == "hybrid":
            agent = JevHybridAgent(self.select, agent)
        self.agent = agent
        return agent

    def select(self, events, state):
        return jev_slots({"text": events[0].payload["text"], **self.injection}, state, self.key)


def state_summary(state):
    return {**base_state_summary(state), "key": state.to_dict()["harmony"]["key"],
            "active_motif": state.current_motif_id}


def evidence(response, target):
    wav = base64.b64decode(response["wav"], validate=True)
    midi = base64.b64decode(response["midi"], validate=True)
    arrangement = pretty_midi.PrettyMIDI(BytesIO(midi))
    with wave.open(BytesIO(wav), "rb") as audio:
        frames = audio.getnframes()
        sample_rate = audio.getframerate()
    target.mkdir(parents=True)
    (target / "band.mid").write_bytes(midi)
    (target / "band.wav").write_bytes(wav)
    return {"midi_sha256": sha256(midi).hexdigest(), "wav_sha256": sha256(wav).hexdigest(),
            "midi": str(target / "band.mid"), "wav": str(target / "band.wav"),
            "midi_tempo": arrangement.get_tempo_changes()[1][0].item(),
            "duration_seconds": frames / sample_rate,
            "notes": {part.name: len(part.notes) for part in arrangement.instruments},
            "bars": response["bars"], "start_bar": response["start_bar"]}


def metric(state):
    return {key: state[key] for key in ("bpm", "next_bar", "playing", "layers", "key", "active_motif")}


def grade(case, row):
    before, after = row["before"], row["after"]
    checks = {"status": row["status"] == case["status"], "bpm": isclose(after["bpm"], case["bpm"]),
              "playback": before["playing"] == after["playing"] == True}
    if case["status"] == "held":
        checks.update(kept_music=metric(before) == metric(after), no_artifact=row["audio"] is None,
                      only_intake=after["state_version"] == before["state_version"] + 1,
                      held_plan=row["plan_status"] == "held",
                      hold_reason=any(token in (row["error"] or "") for token in case["reason_any"]))
        return checks
    audio = row["audio"] or {}
    length = after["next_bar"] - before["next_bar"]
    muted = set(case["inactive"])
    checks.update(advance=length == audio.get("bars") == case.get("bars", length) and length > 0,
                  applied_plan=row["plan_status"] == "applied",
                  rendered_start=audio.get("start_bar") == before["next_bar"],
                  rendered_bpm=isclose(audio.get("midi_tempo", 0), case["bpm"], abs_tol=.02),
                  rendered_wav=audio.get("duration_seconds", 0) > 0,
                  layer_activation={key for key, layer in after["layers"].items() if not layer["active"]} == muted,
                  rendered_layers=all((audio.get("notes", {}).get(name, 0) > 0) == (name not in muted)
                                      for name in ("drums", "bass", "keys")),
                  same_harmony=before["key"] == after["key"], same_motif=before["active_motif"] == after["active_motif"],
                  accepted_once=after["state_version"] == before["state_version"] + 2)
    checks["other_layers"] = all(
        old[prop] < layer[prop] if prop == "level" and name in case.get("raise_level", []) else
        old[prop] > layer[prop] if prop == "density" and name in case.get("lower_density", []) else
        old[prop] == layer[prop]
        for name, old in before["layers"].items() for layer in [after["layers"].get(name, {})]
        for prop in ("level", "density") if prop in layer
    ) and before["layers"].keys() == after["layers"].keys()
    unresolved = (row["plan"] or {}).get("unresolved", [])
    checks["unresolved"] = (any(token in item for token in case["unresolved_any"] for item in unresolved)
                            if "unresolved_any" in case else not unresolved)
    return checks


def run_session(session, mode, config, key, output):
    service = MeasuredService(mode, config, key)
    sid = session["id"]
    state = JamState(session_id=sid, tempo_bpm=session["initial_bpm"])
    done = 0
    results = []
    for case in session["turns"]:
        service.injection = {k: case[k] for k in ("inject_jev",) if k in case}
        service.agent = None
        before = state_summary(state)
        start = perf_counter()
        response, error = None, None
        try:
            response = service.text_turn({"session_id": sid, "request_id": case["id"], "text": case["text"],
                                          "agent": "llm", "bpm": session["initial_bpm"], "profile": "electric",
                                          "after_turn": done})
        except ProviderFailure as exc:
            status, error = "provider_error", exc.kind
        except ValueError as exc:
            status, error = "rejected", str(exc)
        else:
            status = "applied"
            done += 1
        elapsed_ms = round((perf_counter() - start) * 1000, 2)
        trace = getattr(service.agent, "last_trace", {})
        manager = service.works[sid].manager
        state, history = manager.decision_snapshot()
        plan, plan_status = None, None
        if response:
            plan = response["plan"]
            plan_status = manager.plan_status(plan["plan_id"])
        elif trace.get("plan_id") and manager.plan_status(trace["plan_id"]) == "held":
            status = "held"
            plan_status = "held"
            plan = next((item["plan"] for turn in reversed(history) for item in turn["plans"]
                         if item["plan"]["plan_id"] == trace["plan_id"]), None)
        row = {"case": case["id"], "status": status, "error": error, "elapsed_ms": elapsed_ms,
               "trace": trace, "plan_status": plan_status, "plan": plan, "before": before,
               "after": state_summary(state),
               "audio": evidence(response, output / mode / sid / case["id"]) if response else None}
        row["checks"] = grade(case, row)
        row["passed"] = all(row["checks"].values())
        results.append(row)
    return results


def latency(rows):
    times = [row["elapsed_ms"] for row in rows]
    if not times:
        return None
    p95 = quantiles(times, n=20, method="inclusive")[18] if len(times) > 1 else times[0]
    return {"n": len(times), "p50": round(median(times), 2), "p95": round(p95, 2)}


def summarize(rows, mode):
    jev_requests, mini_requests = 0, 0
    models, routes, by_route = set(), Counter(), {}
    for row in rows:
        trace = row["trace"]
        route = trace.get("source", "unknown")
        if route == "llm_fallback":
            route += "/" + trace.get("reason", "unknown")
        routes[route] += 1
        by_route.setdefault(route, []).append(row)
        if mode == "hybrid":
            jev = trace.get("jev")
            if jev and jev.get("source") != "injected":
                jev_requests += 1
                if jev.get("model"):
                    models.add(jev["model"])
            trace = trace.get("fallback", {})
        attempts = trace.get("attempts", [])
        mini_requests += len(attempts)
        models.update(attempt["model"] for attempt in attempts if attempt.get("model"))
    return {"passed": sum(row["passed"] for row in rows), "turns": len(rows),
            "outcomes": dict(Counter(row["status"] for row in rows)), "routes": dict(routes),
            "models": sorted(models), "jev_requests": jev_requests, "mini_requests": mini_requests,
            "all_ms": latency(rows), "successful_ms": latency([row for row in rows if row["passed"]]),
            "successful_artifact_ms": latency([row for row in rows if row["passed"] and row["audio"]]),
            "by_route": {name: {"latency_ms": latency(group), "passed": sum(row["passed"] for row in group),
                                "artifacts": sum(row["audio"] is not None for row in group)}
                         for name, group in by_route.items()}}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--out", type=Path)
    cli.add_argument("--config", type=Path)
    cli.add_argument("--jobs", type=int, default=4)
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args()
    frozen = FIXTURE.read_bytes()
    fixture = json.loads(frozen)
    sessions = fixture["sessions"]
    fingerprint = sha256(frozen).hexdigest()
    if args.dry_run:
        print(json.dumps({"sessions": len(sessions), "turns": sum(len(x["turns"]) for x in sessions),
                          "fixture_sha256": fingerprint, "diagnostics": 1}))
        return
    if args.out is None or args.config is None:
        cli.error("--out and --config are required")
    config = replace(LLMConfig.load(args.config, environ={}), model="gpt-5.4-mini", reasoning_effort="low")
    key = os.environ["OHMYGPT_API_KEY"]
    args.out.mkdir(parents=True)
    rows = []
    path = args.out / "cases.jsonl"
    with path.open("x", encoding="utf-8") as out, ThreadPoolExecutor(max_workers=args.jobs) as pool:
        tasks = [({mode: pool.submit(run_session, group, mode, config, key, args.out)
                   for mode in MODES}, group) for group in [*sessions, fixture["diagnostic"]]]
        for branches, session in tasks:
            arm = {mode: future.result() for mode, future in branches.items()}
            first_divergence = next((case["id"] for case, direct, hybrid in
                                     zip(session["turns"], arm["mini"], arm["hybrid"], strict=True)
                                     if metric(direct["before"]) != metric(hybrid["before"])), None)
            for case, direct, hybrid in zip(session["turns"], arm["mini"], arm["hybrid"], strict=True):
                row = {"session": session["id"], "diagnostic": session == fixture["diagnostic"], "case": case,
                       "first_prestate_divergence": first_divergence, "mini": direct, "hybrid": hybrid}
                rows.append(row)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
            print(session["id"], {mode: sum(row["passed"] for row in arm[mode]) for mode in MODES}, flush=True)
    primary = [row for row in rows if not row["diagnostic"]]
    shared = [row for row in primary if all(row[m]["passed"] for m in MODES)]
    diagnostic = next(row for row in rows if row["diagnostic"])
    summary = {"fixture_sha256": fingerprint, "renderer": "ReferenceService NumPy WAV/MIDI",
               "sessions": len(sessions), "pairs": len(primary),
               "arms": {mode: summarize([r[mode] for r in primary], mode) for mode in MODES},
               "common_success_ms": {mode: latency([r[mode] for r in shared]) for mode in MODES},
               "common_success_artifact_ms": {mode: latency([r[mode] for r in shared if r[mode]["audio"]])
                                              for mode in MODES},
               "first_prestate_divergences": {r["session"]: r["first_prestate_divergence"] for r in primary},
               "failed": {mode: ["/".join((r["session"], r["case"]["id"]))
                                  for r in primary if not r[mode]["passed"]] for mode in MODES},
               "diagnostic": {mode: {"passed": diagnostic[mode]["passed"], "route": diagnostic[mode]["trace"].get("source"),
                                     "reason": diagnostic[mode]["trace"].get("reason"),
                                     "elapsed_ms": diagnostic[mode]["elapsed_ms"]} for mode in MODES}}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
