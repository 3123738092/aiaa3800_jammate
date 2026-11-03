# JamMate workbench revisions

Approved scope: the user's seven-part execution prompt in this chat, 2026-11-03.

Deliver three stages: reproduce/fix existing workflows; expose tempo/key/alignment and evaluate key suggestions; compose rhythm + humming and revise the same passage with local controls/text. Retain the raw input, absolute recording timing, old entry points, MIDI/WAV exports and an honest human-validation boundary. Work in integration/input-band, never push upstream or merge main.

Approach: retain existing input pages and reuse them as input panels in a new session page. Explicitly add each recognized input to the session; recording replacement alone must not overwrite committed materials. The parent accepts only same-origin messages from its own frames. No microphone recordings are persisted automatically.

Use the existing InputEvent/StateManager/RuleAgent/BandPlan/Runtime. Each web render is a revision of the same passage starting at bar zero, not advancing live playback. A candidate StateManager starts from the last successful musical state, with explicitly selected tempo/key and fresh material catalog. It commits only after rendering succeeds and its draft revision is still current. Draft sources/settings and result cache are transport metadata, not a second independent music state. Draft revisions invalidate in-flight output; request IDs prevent applying controls twice. Keep the last two successful outputs in the UI for comparison. Sessions are process-local and bounded; report expiry/restart explicitly.

Tempo defaults to 100 only with an explicit default label; rhythm can supply tempo, manual selection overrides. Melody timing is `(recording seconds - chosen recording origin) + placement beats * 60/BPM`; reject any mapping that would drop notes. No quantization/stretch/transposition. Existing profile-based key estimation supplies suggestions only, never an automatically asserted key; show ambiguity. Unknown key uses melody + unpitched drums, with pitched layers unavailable until a key is selected. Explicit key uses existing harmony progression, not a claim of automatic chord fitting.

Alternatives considered: extending each independent page would duplicate session state; a full new recorder would duplicate tested input code; a live streaming redesign exceeds the approved per-round scope. Embedded existing inputs plus revision rendering reuses working code and keeps timing explicit.

Local rule text is supported by the existing grammar, with unsupported requests rejected atomically. No real LLM call without additional authorization/configuration. No physical microphone or human-quality claims from synthetic tests. Final handoff includes runnable workbench, outputs, regression results and manual acceptance steps.
