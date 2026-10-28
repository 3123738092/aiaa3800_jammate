# Integration and four review rounds

## Frozen integration baseline

Local commit/tag: `3746ce4` / `integration-baseline`. Imported stable humming source without changing its detector; added InputEvent adapter, shared local portal, Python rhythm event path and legacy rhythm adapter. Python: 234 tests passed. JS: 10 tests passed. Browser: humming model ran, JSON/InputEvent/MIDI matched; rhythm recovered `[0, 1, 1.5, 3]` at 100.45 BPM; no page errors.

The original five-note synthetic example yielded 8 raw / 7 cleaned notes in this browser run, with the first two expected notes absent. This is a failure observation, not a valid quality benchmark pass.

## Core humming continuity implementation

Added independent single-voice frame decoding, pitch persistence, acoustic rest protection, short detection-gap bridging, and raw-output comparison. No key/tempo guessing or beat quantization. Default 140 ms confirmation for unarticulated pitch change, 35 ms for strong articulated changes, 90 ms maximum detection gap, 70 ms minimum note. These are initial heuristics, not calibrated human thresholds.

JS: 17 tests passed (including seven targeted continuity cases). Browser: optimized five-note example recovered `[60,64,67,64,62]`, raw 14 / selected 5; MIDI and InputEvent matched. Raw model output differed from baseline's run although weights were unchanged. Investigate execution-backend consistency next. The baseline directory remains frozen.

Real human humming recordings have not been supplied for this run. All quality evidence below must distinguish synthetic audio, constructed model frames, and human observations.
