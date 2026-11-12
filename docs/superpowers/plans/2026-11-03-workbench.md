# JamMate workbench implementation plan

> Execute inline with superpowers:executing-plans. Track completion and decisions in the plan ledger.

**Goal:** reliable existing flows, explicit musical settings, and revision-based multi-input sessions.
**Architecture:** reuse input pages and existing orchestration; draft metadata plus transactional candidate StateManager; render the same passage from bar zero.
**Tech Stack:** existing Python/NumPy/FluidSynth and browser JavaScript/Basic Pitch; no new framework.
**Spec:** docs/superpowers/specs/2026-11-03-workbench-design.md

## Global Constraints
- Preserve raw inputs, MIDI timing, source attribution and existing routes.
- No auto quantization, key guessing presented as certainty, private recording collection, cloud calls, or main/upstream writes.
- Prefer changes justified by a reproduced failure; verify actual notes/audio, not only state labels.

## Review Focus
- Pending microphone permission resolves after page exit: tracks must stop.
- Browser back/forward restoration retains playable media and retryable controls.
- A newer draft or reset arrives during rendering: no stale commit/output.
- Retried controls must not double-apply; failed rendering must not commit musical state.
- Time origin beyond an early note, ambiguous key and conflicting text must fail without silent loss.

### Task 1: Existing input lifecycle and evidence
Files: tests/input-navigation.mjs; web/{baseline,optimized}/src/app.js; web/rhythm.js; web/band-panel.js; fixtures/benchmark tools as warranted.
Interfaces: existing input event and audio controls unchanged.
- [ ] Run Python, JS, browser baseline; save results.
- [ ] Reproduce page-exit/pending-permission and back/forward failures in real browser tests.
- [ ] Fix only demonstrated lifecycle causes; rerun browser lifecycle/navigation and build.
- [ ] Evaluate fixed synthetic continuity cases plus light/noisy variants; preserve failures as evidence.
- [ ] Commit verified stage.

### Task 2: Explicit musical settings and reusable render service
Files: src/jammate/web_session.py (new), input_band.py, input_server.py, orchestration/manager.py, contracts/adapters.py, generate/arranger.py, generate/rhythm.py, orchestration/offline.py; tests/test_web_session.py (new).
Interfaces: SessionStore.create() -> session_id; update(session_id, revision, draft) -> summary; render(session_id, revision, request_id, text='') -> WAV/MIDI/plan/state/settings; reset uses new session ID. draft={inputs:{humming?,rhythm?},settings:{tempo_source,bpm,key,melody_origin_s,placement_beats}}. Optional key estimate returns score/margin/ambiguity, never auto-applies.
- [ ] Write failing behavioral tests for manual/detected tempo, explicit/unknown key, offset preservation/rejection, unchanged lead at differing BPM, scoped edits and output MIDI.
- [ ] Add explicit placement mapping to intake; retain event evidence unchanged.
- [ ] Add unknown-key drum-only execution with no invented harmony; reuse key estimator for suggestions.
- [ ] Implement bounded revision sessions and atomic render commit; preserve legacy render_clip behavior.
- [ ] Test duplicate/conflicting requests, stale renders, invalid inputs and failed retry using actual candidate managers.
- [ ] Run affected and full Python suites; commit verified stage.

### Task 3: Browser session and handoff
Files: web/session.{html,js,css} (new); web/band-panel.js; web/index.html; tests/session-browser.mjs (new); docs/integration/SESSION.md and evidence.
Interfaces: child postMessage {type:'jammate-input',event} accepted from registered frames only. Session APIs from Task 2; UI draft revision increments on edits; late results ignored. Preserve two outputs with explicit versions; no recorded audio in storage.
- [ ] Write browser test for combined inputs, controls, text rejection, key suggestion, stale response, reset, retries and mobile width.
- [ ] Add session page with input panels, clear tempo/key/origin/placement controls, add/remove materials and local control buttons.
- [ ] Wire revisions/API, stale-response guards, downloads and output comparisons; keep originals alive in frames.
- [ ] Run real browser model -> session -> FluidSynth -> downloaded MIDI/WAV validation; generate shareable synthetic artifacts.
- [ ] Run full regressions; review whole branch with a fresh reviewer; address important findings with regressions.
- [ ] Document settings, session limitations, exact test results and human acceptance script; commit and push own branch; verify served workbench.
