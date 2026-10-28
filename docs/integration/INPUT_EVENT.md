# InputEvent integration profile

The SPEC envelope is implemented in `src/jammate/input_event.py`. This is a perception-only profile, not a new shared musical state. It does not produce strategy decisions or mutate JamState. Humming emits `source=humming,event_type=musical_phrase`; rhythm emits `source=rhythm,event_type=rhythm_seed`. A speech/text envelope can be validated, but speech recognition, instrument input and automatic modality routing remain unfinished.

| Field | Meaning |
|---|---|
| event_id | Identifier for this perception result. Browser humming variants have separate IDs. |
| session_id | Identifies the clock the envelope uses. Required, nonempty. |
| started_at / ended_at | Finite nonnegative seconds on that session clock; end is strictly later. |
| payload | Source-specific data, with extension fields allowed. |
| confidence | Nullable number in [0,1]. Humming is null; rhythm is a grid-fit heuristic, not calibrated correctness. |

The optimized browser lab starts a **new session for each imported/recorded clip**, at zero. Reprocessing that clip keeps its session. It cannot infer where unrelated imported files belong on a shared runtime clock. The frozen baseline has its original tab session semantics and must not be used to align unrelated clips. For an existing runtime session, use the CLI's `--session-id` and `--started-at`, or the Python adapter with those explicit values.

For both audio sources, `payload.timebase=seconds_from_recording_start`. A note at `start_sec=0.4` in an event with `started_at=23` occurs at session second **23.4**. Trimming restores the original recording offset before export. MIDI conversion does not change these seconds. `tempo_bpm=null` for free humming: a MIDI serialization tempo is not an inferred musical tempo.

Humming `notes` contain integer `pitch_midi` 0–127, nonnegative `start_sec`, positive `duration_sec`, optional `velocity` 0–1. Notes must fit inside the event duration. Raw polyphonic output may overlap; the continuity variant assumes one voice. Empty detections are `status=no_notes`, with an empty list. Raw, continuous and MIDI export reflect the current UI selection; original audio and legacy diagnostic JSON remain separate downloads.

Rhythm retains the upstream validated `RhythmContext` under `payload.rhythm`: 60–180 BPM, one 4/4 bar, sorted `onset_beats`, matching accents. `onsets_sec` are **quantized** times reconstructed from bar origin and beats, not raw detector timestamps. The validator verifies their bounds and agreement with the rhythm clock. `to_legacy_rhythm` bridges to existing UserRhythm consumers without a global-state migration.

Call `validate_event` before passing external data downstream, or POST JSON to `/api/validate` in the local lab. It checks envelope, source/type pairing, finite JSON, note bounds, velocity, and rhythm consistency. `humming-json` accepts both selected-note exports and diagnostic documents containing `results`. The tests include malformed envelopes and adapter round trips.
