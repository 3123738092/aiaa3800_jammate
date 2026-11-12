"""Opt-in REAL model/browser acceptance, separate from pytest.

Start a workspace server with configured MRT2 weights + LLM + FluidSynth.
python tests/mrt2_browser.py --url http://127.0.0.1:4192 --out demo_out/browser
This script performs real MRT2 inference and calls the real configured LLM.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:4192")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = {"mode": "real_services_browser_decode_not_physical_playback", "cases": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 1000})
        page.goto(args.url + "/text-creation.html")
        page.wait_for_function("!document.querySelector('#text-agent option[value=llm]').disabled")
        page.locator("summary").click()
        page.locator("#text-renderer").select_option("mrt2")
        page.locator("#creation-prompt").fill("鼓更猛一点")
        with page.expect_response(lambda r: r.url.endswith("/text/turn")) as response:
            page.locator("#text-generate").click()
        rejected = response.value.json()
        assert "MRT2 cannot guarantee" in rejected["error"]
        report["cases"].append({"case": "strict_rejection", "error": rejected["error"]})
        page.wait_for_function("!document.querySelector('#text-preview').disabled")
        page.locator("#text-preview").check()
        with page.expect_response(lambda r: r.url.endswith("/text/turn"), timeout=240000) as response:
            page.locator("#text-generate").click()
        generated = response.value.json()
        assert response.value.ok, generated.get("error")
        receipt = generated["receipt"]
        assert receipt["inference"] == "real_magenta_rt"
        assert receipt["phase"] == "preview" and not receipt["state_committed"]
        page.wait_for_function("document.querySelector('#text-status').textContent.includes('音乐状态未提交')")
        assert "条件 MIDI" in page.locator("#text-midi").inner_text()
        decoded = page.evaluate("""async () => {
          const context = new AudioContext();
          const data = await (await fetch(document.querySelector('#text-audio').src)).arrayBuffer();
          const audio = await context.decodeAudioData(data);
          const result = {duration:audio.duration, channels:audio.numberOfChannels, sampleRate:audio.sampleRate};
          await context.close(); return result;
        }""")
        report["cases"].append({"case": "rule_mrt2_preview", "receipt": receipt, "decoded": decoded,
                                "decision_trace": generated["decision_trace"]})
        page.screenshot(path=str(args.out / "mrt2-preview.png"), full_page=True)

        page.locator("#new-work").click()
        page.locator("#text-renderer").select_option("default")
        page.locator("#text-agent").select_option("llm")
        page.locator("#creation-prompt").fill("写一小段让我能接着哼唱的旋律，速度保持100 BPM。")
        with page.expect_response(lambda r: r.url.endswith("/text/turn"), timeout=90000) as response:
            page.locator("#text-generate").click()
        generated = response.value.json()
        assert response.value.ok, generated.get("error")
        assert generated["decision_trace"]["source"] == "llm"
        assert generated["receipt"]["state_committed"]
        report["cases"].append({"case": "real_llm_fluidsynth", "receipt": generated["receipt"],
                                "decision_trace": generated["decision_trace"], "plan": generated["plan"]})
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(args.out / "llm-mobile.png"), full_page=True)
        browser.close()
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print("Passed: strict rejection, real rule/MRT2 preview, real LLM/FluidSynth, browser WAV decode, mobile layout")


if __name__ == "__main__":
    main()
