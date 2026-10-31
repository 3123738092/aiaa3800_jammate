"""Recompute the diagnostic summary and optional figure from saved evidence."""
import argparse
import json
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET


def summarize(batch, output, plot=None):
    read = lambda path: json.loads((batch / path).read_text(encoding="utf-8"))
    before = read("baseline.json")
    after = read("context_after.json")
    large = read("controlled_300/report.json")
    compact = read("controlled_300_compact_history/report.json")
    llm = read("llm_26/evaluation.json")
    humming = read("humming_probe/comparison.json")
    rows = compact["turns"]
    latencies = [r["decision_ms"] for r in llm["turns"] if r["source"] == "llm"]
    suite_file = batch / "full_suite_final.xml"
    if not suite_file.exists():
        suite_file = batch / "full_suite.xml"
    suite = ET.parse(suite_file).getroot().find("testsuite")
    summary = {"kind":"development diagnostics; controlled soak and real API results are separate",
        "baseline":before, "context_after":after,
        "controlled": {"rounds":compact["rounds"], "passed":compact["passed"], "real_api_calls":0,
            "export_failures_recovered":sum(r["failed_export_recovered"] for r in rows),
            "replaced_plans":sum(r["replaced_plan"] for r in rows), "network_fallbacks":sum(r["source"] == "fallback" for r in rows),
            "invalid_responses_repaired":sum(r["attempts"] > 1 for r in rows), "stale_events":15,
            "initial_request_chars_max":max(r["request_chars"] for r in rows),
            "diagnostics":compact["diagnostics"], "final_state_version":compact["final_state_version"],
            "retained_python_bytes_before":large["turns"][-1]["memory_bytes"],
            "retained_python_bytes_after":rows[-1]["memory_bytes"],
            "memory_limit":"tracemalloc retained Python allocations; not process RSS. Evidence and catalogs still grow linearly; not a bounded-memory production system."},
        "real_llm": {"passed":llm["passed"], "total":llm["total"], "cases_sha256":llm["cases_sha256"],
            "failed_turns":[r for r in llm["turns"] if not r["passed"]],
            "llm_decisions":sum(r["source"] == "llm" for r in llm["turns"]),
            "system_controls":sum(r["source"] == "system_control" for r in llm["turns"]),
            "successful_decision_latency_ms":{"min":min(latencies),"median":statistics.median(latencies),"max":max(latencies)},
            "max_initial_request_chars":max(r["request_chars"] or 0 for r in llm["turns"])},
        "humming":humming, "tests":dict(suite.attrib)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1,3,figsize=(13,4.1),layout="constrained")
        old = before["context_growth_before"]
        for data, label, color in [(old,"Full music catalog","#bd5149"),(after,"Bounded read view","#2c7e92")]:
            axes[0].plot([r["motifs"] for r in data],[r["context_chars"]/1000 for r in data],"o-",label=label,color=color)
        axes[0].set(xlabel="Registered motifs",ylabel="Context (thousand characters)",title="LLM music context")
        axes[0].legend(fontsize=8)
        for data,label,color in [(large,"Full state snapshots","#bd5149"),(compact,"Compact history snapshots","#2c7e92")]:
            axes[1].plot([r["turn"] for r in data["turns"]],[r["memory_bytes"]/1e6 for r in data["turns"]],label=label,color=color)
        axes[1].set(xlabel="Controlled turns",ylabel="Retained Python allocations (MB)",title="History memory diagnostic")
        axes[1].legend(fontsize=8)
        axes[2].bar([r["turn"] for r in llm["turns"]],[r["decision_ms"]/1000 for r in llm["turns"]],
                    color=["#bd5149" if not r["passed"] else "#2c7e92" for r in llm["turns"]])
        axes[2].set(xlabel="Real API conversation turn",ylabel="Decision time (seconds)",title="26-turn development conversation")
        axes[2].annotate("Timeout -> hold",xy=(7,20),xytext=(11,18),arrowprops={"arrowstyle":"->"},fontsize=8)
        for ax in axes:
            ax.grid(axis="y",alpha=.2)
        fig.suptitle("JamMate diagnostics | synthetic / controlled inputs; no physical playback acceptance",fontsize=11)
        plot.parent.mkdir(parents=True,exist_ok=True)
        fig.savefig(plot,dpi=160)
        plt.close(fig)
    print(json.dumps({"summary":str(output),"controlled_passed":compact["passed"],"real_llm":f"{llm['passed']}/{llm['total']}"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch",required=True,type=Path)
    parser.add_argument("--out",required=True,type=Path)
    parser.add_argument("--plot",type=Path)
    args = parser.parse_args()
    summarize(args.batch,args.out,args.plot)
