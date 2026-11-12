# Integration and four review rounds

## Frozen integration baseline

Local commit/tag: `3746ce4` / `integration-baseline`. Imported stable humming source without changing its detector; added InputEvent adapter, shared local portal, Python rhythm event path and legacy rhythm adapter. Python: 234 tests passed. JS: 10 tests passed. Browser: humming model ran, JSON/InputEvent/MIDI matched; rhythm recovered `[0, 1, 1.5, 3]` at 100.45 BPM; no page errors.

The original five-note synthetic example yielded 8 raw / 7 cleaned notes in this browser run, with the first two expected notes absent. This is a failure observation, not a valid quality benchmark pass.

## Core humming continuity implementation

Added independent single-voice frame decoding, pitch persistence, acoustic rest protection, short detection-gap bridging, and raw-output comparison. No key/tempo guessing or beat quantization. Default 140 ms confirmation for unarticulated pitch change, 35 ms for strong articulated changes, 90 ms maximum detection gap, 70 ms minimum note. These are initial heuristics, not calibrated human thresholds.

JS: 17 tests passed (including seven targeted continuity cases). Browser: optimized five-note example recovered `[60,64,67,64,62]`, raw 14 / selected 5; MIDI and InputEvent matched. Raw model output differed from baseline's run although weights were unchanged. Investigate execution-backend consistency next. The baseline directory remains frozen.

Real human humming recordings have not been supplied for this run. All quality evidence below must distinguish synthetic audio, constructed model frames, and human observations.

## Round 1 — Repeatable inference

Evidence: two fresh browser runs of identical weights and audio produced raw 8 versus 14 notes; the first lost the initial C/E. This also appeared in the prior exploration log. The exact WebGL/driver cause is not established.

Change: optimized version uses TensorFlow.js CPU as a reference execution backend and records it in exported diagnostics. Baseline keeps its original backend choice. Tradeoff: longer processing time, especially on long recordings.

Validation: two consecutive CPU conversions returned byte-for-byte equal note objects and all five expected pitches [60,64,67,64,62]; InputEvent and MIDI matched, rhythm still passed. Evidence: round-1-browser.json. No claim of universal backend determinism or human accuracy.

## Round 2 — Preserve articulated short notes

Evidence: a constructed frame case with a 100 ms D between C and E, plus residual C activation at 90% of D, incorrectly returned only C/E. Pitch hysteresis masked D before its articulation could be evaluated. This regression failed before the change.

Change: strong-onset candidates can enter the existing persistence check despite residual previous-pitch activation; weak changes still use hysteresis. This is a focused change to continuity policy 1.1.0. A strong false onset could still promote a pitch error; raw output remains available.

Validation: 18 optimized JS tests passed. Five complete synthetic audio cases (sustain with vibrato, same-pitch repeats, ascending steps, rest, articulated short note) passed through the actual browser model. Expected counts 1/3/4/2/3; optimized counts 1/3/4/2/3; baseline counts 13/8/5/5/5 in the recorded run. Maximum matched boundary error was 9.76 ms. These five examples are not an accuracy estimate on human recordings. Benchmark and generator: humming-benchmark.json, tests/humming-benchmark.mjs, examples/make_humming_benchmark.py.

## Round 3 — Reliable event contract and clock meaning

Evidence: seven new regression cases failed: notes-only Hum Lab exports raised KeyError, four invalid velocities passed, missing rhythm data raised an unnormalized error, and quantized onset seconds could contradict beat positions. Review also found unrelated browser clips shared a session ID while every clip started at zero, and exported status could follow raw notes instead of the selected variant.

Change: repair the adapter and validate velocity/rhythm consistency; use a separate session for each standalone clip while keeping its ID during reprocessing; make selected export status consistent; document session versus recording seconds and the existing-runtime adapter. No inferred alignment is imposed on unrelated recordings.

Validation: all 15 Python InputEvent cases and 19 optimized JS cases pass. Browser download validation, MIDI correspondence, repeated conversion and independent rhythm-clip sessions pass with no page errors (round-3-browser.json). Baseline source remains frozen. The local lab still does not share a transport clock with the legacy live runtime; explicit session/offset arguments are required there.

## Round 4 — Safe audio replacement and recording lifecycle

Evidence: two clicks while the microphone permission promise was unresolved made two capture requests (regression observed: 2, expected 1). Import/record controls also remained available during recording, and failed decoding could leave the previous audio player's source visible after its analysis buffer was cleared.

Change: explicit requesting/recording/stopping/loading/analyzing states lock incompatible operations. Failed replacement removes old audio/results; stop/error/page exit releases tracks. Added an original-audio download to preserve rhythm recordings before closing. Settings changes invalidate the old event.

Validation: browser regression now passes permission-pending lock, denied-permission recovery, mocked recording/track release, corrupt-file clearing, successful reload/reanalysis, setting invalidation and mobile-width checks with no page errors. Evidence: round-4-browser.json; no physical microphone was used. Real device-specific capture behavior remains a manual acceptance check.
