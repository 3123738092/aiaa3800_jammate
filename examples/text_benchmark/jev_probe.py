"""Measure Jev's intent and parameter selection on fixed JamMate cases."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
from statistics import median, quantiles
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


URL = "https://api.ohmygpt.com/v1/systemone"
MODEL = "jev-latest"
ROUTES = {
    "fast": "Every requested change is unambiguous, compatible, and can be executed as an ordinary BandPlan. "
            "Supported here: set 60–180 BPM, signed relative BPM within that range, restore an explicitly "
            "indexed tempo found in the supplied committed history, start/stop, hold, layer on/off, "
            "layer volume or density decrease, add an acoustic guitar, choose a 1–16 bar clip length, "
            "preserve explicitly named properties, and combine these when compatible. "
            "A clear revision ('不对', 'actually', 'make that') replaces the abandoned value.",
    "agent": "Requires interpretation beyond this fast path, such as musical imagery, note-specific editing, "
             "motif responses, incomplete historical evidence or a mixture of executable and unsupported requests.",
    "clarify": "Two simultaneous requirements directly conflict, the target has multiple plausible references, "
               "or the user requests an unambiguous numerical tempo outside 60–180 BPM.",
    "unsupported": "Its only concrete effect cannot be done by the current renderer, such as changing key, "
                   "adding an unavailable instrument or setting reverb.",
}
SCOPES = {
    "none": "There is no explicit layer change. Tempo and keep constraints do not count.",
    "drums": "Change the drum layer only.",
    "bass": "Change the bass layer only.",
    "keys": "Change the keyboard layer only.",
    "lead": "Change the lead or melody only.",
    "guitar": "Change or add the guitar layer only.",
    "multiple": "Change more than one layer, or the whole band sound.",
    "unclear": "The requested layer cannot be determined, or it is outside the supported layer catalogue.",
}
NUMBER = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])")


def choice(instruction, criteria):
    return {"type": "choice", "instructions": instruction, "criteria": dict(criteria)}


def request_body(case):
    text = case["text"]
    tempo = {
        "none": "No explicit tempo instruction. Do not count bar counts, MIDI velocity or beat positions as BPM.",
        "keep": "Explicitly keep the current session BPM, including a negated tempo change.",
        "restore": "Restore the tempo from the explicitly identified committed historical state.",
        "unclear": "A tempo request is present but contradictory, lacks a number, or lacks a resolvable reference.",
    }
    for i, match in enumerate(NUMBER.finditer(text), 1):
        token = match.group()
        tempo.update({f"set_{i}": f"Set the BPM to {token}, using number occurrence {i} in the user text.",
                      f"up_{i}": f"Increase the current BPM by {token}, using occurrence {i}.",
                      f"down_{i}": f"Decrease the current BPM by {token}, using occurrence {i}."})
    return {
        "model": MODEL,
        "state": {"current_user_request": text, "jam_state": case.get("state", {"tempo_bpm": 100}),
                  "committed_history": case.get("history", [])},
        "questions": {
            "route": choice("Which path covers the entire current request? Check the active constraints, "
                            "corrections and state. Only the current request should be executed.", ROUTES),
            "tempo": choice("Which tempo instruction is present in the current request? "
                            "Use the final correction and distinguish tempo from beat position.", tempo),
            "scope": choice("Which layer is the requested target? Ignore mentions of layers to preserve.", SCOPES),
        },
    }


def validate_cases(cases):
    if not cases or len({row["id"] for row in cases}) != len(cases):
        raise ValueError("case IDs must be unique and nonempty")
    for case in cases:
        questions = request_body(case)["questions"]
        for field, value in case["expected"].items():
            if value not in questions[field]["criteria"]:
                raise ValueError(f"{case['id']}: {field} has no candidate for {value}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run_case(case, key, *, body=None):
    if body is None:
        body = request_body(case)
    request = Request(URL, json.dumps(body, ensure_ascii=False).encode(),
                      {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    start = perf_counter()
    row = {"case": case}
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            result = json.load(response)
        answers = result["answers"]
        selected = {name: answers[name]["choice"] for name in body["questions"]}
        for name, value in selected.items():
            if value not in body["questions"][name]["criteria"]:
                raise ValueError("unknown choice")
        row.update(model=result["model"], usage=result.get("usage"), answers=answers, selected=selected,
                   matched=all(selected[k] == v for k, v in case["expected"].items()))
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        row["error"] = f"HTTP {exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__
    row["elapsed_ms"] = round((perf_counter() - start) * 1000, 1)
    return row


def summary(rows):
    good = [row for row in rows if "error" not in row]
    latency = [row["elapsed_ms"] for row in good]
    fields = ("route", "tempo", "scope")
    by_field = {field: [row for row in good if field in row["case"]["expected"]] for field in fields}
    gates = {}
    for cutoff in (0.0, 0.8, 0.9, 0.95):
        accepted = [row for row in good if row["selected"]["route"] == "fast"
                    and row["answers"]["route"]["confidence"] >= cutoff]
        gates[str(cutoff)] = {"accepted": len(accepted),
                              "matched_every_scored_field": sum(row["matched"] for row in accepted),
                              "false_fast_routes": sum(row["case"]["expected"]["route"] != "fast"
                                                       for row in accepted)}
    return {
        "cases": len(rows), "api_responses": len(good), "models": sorted({row["model"] for row in good}),
        "exact": sum(row["matched"] for row in good),
        "per_question": {field: {"correct": sum(row["selected"][field] == row["case"]["expected"][field]
                                              for row in scored), "total": len(scored)}
                         for field, scored in by_field.items()},
        "route_confidence_gates": gates, "p50_ms": round(median(latency), 1) if latency else None,
        "p95_ms": round(quantiles(latency, n=20, method="inclusive")[18], 1) if len(latency) > 1 else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("jev_cases.json"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    validate_cases(cases)
    if args.dry_run:
        print(f"Validated {len(cases)} case definitions")
        return
    if args.out is None or not os.environ.get("OHMYGPT_API_KEY"):
        parser.error("a new --out file and OHMYGPT_API_KEY are required")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as output:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            rows = list(pool.map(lambda case: run_case(case, os.environ["OHMYGPT_API_KEY"]), cases))
        report = {"summary": summary(rows), "rows": rows}
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
