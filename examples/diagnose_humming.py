"""Read-only 2x2 decoder experiment, including repeated same-pitch controls.

prepare/evaluate use the JamMate Python environment; infer needs Playwright.
No source handoff file is changed; only the local HTTP response is overridden.
"""
import argparse
import hashlib
import json
from pathlib import Path


def prepare(args):
    from prepare_acceptance_inputs import prepare as make_inputs
    data = json.loads(Path(__file__).with_name("acceptance_cases.json").read_text(encoding="utf-8"))
    data["humming"] += [
        {"id":"h_repeated", "notes":[[60,.4+i*.7,.4] for i in range(5)], "duration_s":4.3},
        {"id":"h_rearticulation", "notes":[[60,.4+i*.48,.40] for i in range(6)], "duration_s":3.8}]
    args.batch.parent.mkdir(parents=True, exist_ok=True)
    cases = args.batch.with_name(args.batch.name + "_cases.json")
    with cases.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    make_inputs(args.batch, cases)


def infer(args):
    from transcribe_acceptance_humming import transcribe
    if args.dist is None:
        raise ValueError("infer needs --dist pointing to the unchanged humming handoff")
    for name, disabled, threshold in (("baseline",False,None), ("infer_off",True,None),
                                      ("threshold_high",False,.5), ("both",True,.5)):
        transcribe(args.batch, args.dist, output=args.batch/name,
                   disable_inferred_onsets=disabled, onset_threshold=threshold)


def evaluate(args):
    from run_acceptance_batch import humming_metrics, write
    manifest = args.batch / "manifest.json"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert digest == (args.batch / "manifest.sha256").read_text().strip()
    cases = json.loads(manifest.read_text(encoding="utf-8"))
    report = {"manifest_sha256":digest, "kind":"synthetic diagnostic, not a production threshold decision", "variants":{}}
    for variant in ("baseline", "infer_off", "threshold_high", "both"):
        folder = args.batch / variant
        run = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        qualities = {r["id"]:r for r in run["cases"]}
        rows = []
        for case in cases["humming"]:
            row = {"id":case["id"]}
            if qualities[case["id"]]["quality"] == "rejected":
                row.update(rejected=True, passed=case.get("expect_rejected",False))
            else:
                document = json.loads((folder / (case["id"]+"_cleaned.json")).read_text(encoding="utf-8"))
                row.update(humming_metrics(case, document, cases["protocol"]))
                row["cleanup_edits"] = len(document["results"]["cleaned"]["edits"])
            rows.append(row)
        report["variants"][variant] = {"overrides":run["diagnostic_overrides"], "passed":sum(r["passed"] for r in rows),
              "total":len(rows), "original_bundle_sha256":run["original_bundle_sha256"],
              "served_bundle_sha256":run["served_bundle_sha256"], "model_sha256":run["model_sha256"], "cases":rows}
    assert len({r["original_bundle_sha256"] for r in report["variants"].values()}) == 1
    write(args.batch / "comparison.json", report)
    print(json.dumps({k:{"passed":v["passed"],"total":v["total"],"failures":[r["id"] for r in v["cases"] if not r["passed"]]} for k,v in report["variants"].items()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare","infer","evaluate"), required=True)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    {"prepare":prepare,"infer":infer,"evaluate":evaluate}[args.stage](args)
