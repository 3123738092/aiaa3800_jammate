# Integration sources

- Upstream rhythm/runtime prototype: `3123738092/aiaa3800_jammate`, commit `e6affe8992bab073688f132f07c6a7f5950768af`. Existing code and sample artifacts are retained. Full upstream history remains in the local clone; GitHub API delivery imports a snapshot with explicit source attribution and separate integration/review commits. Existing Decision/JamState are legacy implementations; this task does not claim a completed BandPlan migration.
- Wenhan's Hum Lab stable source: local `jammate-hum-demo`, commit `6ea345707b9917b77b596cd0963db34c2ae6f0bb`. Imported under `web/baseline`; Spotify Basic Pitch / TensorFlow.js / Tonejs MIDI package versions retained.
- Explore version `6da1f05273766cb4bf83ed98d31b04ec3a1a745e` was reviewed. Its rule-response generator is outside perception scope; importing it would blur module boundaries. Stable demo chosen for a controlled detector comparison.
- User-supplied SPEC is preserved as `SPEC.md`. No Notion writes or connection changes are part of this implementation.
- `integration-baseline` freezes integration before detector changes. Optimized source lives in a separate directory; both run from the same server.

Progress at intake: two working prototypes with separate JSON structures; no standard InputEvent in upstream; target repository empty. Humming supports 20-second capture / 30-second import, not a 3–4-second hard limit. Old cleanup only merges weak short same-pitch tails after an already long note. Real-world quality was not previously established.
