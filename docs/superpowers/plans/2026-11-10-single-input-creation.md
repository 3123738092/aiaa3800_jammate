# Single-input creation implementation plan

> Execute inline with superpowers:executing-plans. The approved goal prompt on 2026-11-10 is the specification.

**Goal:** Let humming, rhythm, and then text independently inspire a new playable passage.
**Architecture:** The existing input pages pass one InputEvent to a creation route. An opt-in RuleAgent decision feeds the same StateManager, BandPlan and WAV/MIDI renderers as the previous accompaniment route. Text creates its own speech event and fresh state.
**Tech stack:** Python, existing Basic Pitch, browser JavaScript, MIDI and FluidSynth.

## Global constraints

- Start at f30b855 on integration/single-input-creation. Preserve other branches.
- Each request uses one independent source. Keep existing recognition, original playback, and the legacy band endpoint.
- Pass the two audio browser flows before implementing text. Use synthetic fixtures for automated checks.

## Review focus

- A single sustained note can still inspire a changed response. Original notes remain available for comparison.
- Rhythm retains detected BPM and source anchors. Decoration must appear in exported notes for a rhythm with gaps.
- Old audio tasks must not overwrite a newer take after navigation. Retry a failed generation with the same selected source.
- Text must work from an empty musical state and change a measurable output attribute. Unsupported text must fail explicitly.
- Opening another input must not use the previous input's material or output.

## Task 1 — Independent audio creation

Files: src/jammate/input_band.py, src/jammate/input_server.py, src/jammate/orchestration/agent.py, web/band-panel.js, web/optimized/src/app.js, web/rhythm.js, web/index.html, tests/test_input_creation.py, tests/input-browser.mjs. Port the applicable lifecycle changes in both humming pages and the rhythm page, plus the focused async browser regression.

- [x] Add failing MIDI regressions for creative humming and rhythm. Assert changed lead with a recognizable phrase relationship and input rhythm anchors with added positions. Check that the legacy response still echoes the humming input.
- [x] Add optional creative selection to the existing planner and render entry. Route POST /api/create to it and use the existing VARY_MOTIF and rhythm VARY paths. Keep a fresh manager per request.
- [x] Wire the two normal input pages to the shared result panel with creation copy. Keep their original audio/recognition preview and WAV/MIDI exports.
- [x] Reproduce the stale analysis/decode regression and port only the applicable guards. Rebuild both frontends.
- [x] Exercise both normal pages using actual browser recognition and rendering. Decode WAV, read MIDI, compare output to the recognized input, verify retry and source isolation, save synthetic media. Commit the audio milestone.

## Task 2 — Independent text creation

Files: src/jammate/input_band.py, web/text-creation.html, web/text-creation.js, web/band-panel.js, web/index.html, tests/test_input_creation.py, tests/creation-text-browser.mjs.

- [x] After Task 1 passes, add failing tests for creation with just “来一段，慢一点” and “来一段，快一点”, plus explicit rejection of unsupported text. Compare the resulting MIDI tempos.
- [x] Let POST /api/create accept a text-only request and normalize it with from_text. Require INITIATE with no unresolved clauses. Render through the same staged plan executor.
- [x] Add the text page with clear supported examples and the same output panel. Exercise it after switching from an audio entry. Verify editing and retry cannot reuse old material.
- [x] Save text and audio examples, add the operating instructions and validation evidence, and run full applicable regressions. Review the new branch and verify the served preview.
