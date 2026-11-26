# JamMate Listening Room

## Intent

An ordinary listener should be able to point to a musical object and talk to that part of the band. The supplied Listening Room HTML informs target-based interaction; it is a design reference, not an executable integration contract.

## Composition

Three spatial objects: drum, microphone and small keyboard for melody. Full-band scope is an explicit control, independent of the objects. A circular Cover Flow-style carousel centres the focused object with scale, depth and lighting. One composer follows the target. Speech, humming and clapping are explicit input choices; capture review appears in place. Detailed pads and keyboard remain under the optional keyboard control.

Settings use a dialog grouped into performance, sound and input. Inspector uses a graphite dialog with actual processing trace, music state and raw-data tabs. Native dialog focus handling, Escape dismissal and keyboard-accessible tabs are provided.

## Material and typography

Dark workspace #101012, local glass control surfaces and restrained violet reflections. Text uses clear light neutral roles; accents belong to selection and actual recording state. Original GLB microphone uses PBR metal and studio HDRI; static fallback is an offline rendering of the same model. No full-page 3D.

Locally served DM Sans / Noto Sans SC and system UI fallbacks; Geist Mono for JSON, with OFL licenses. Natural text spacing; monospace only for raw JSON and secondary hardware legends. No logo mark, slogan or decorative diagonal arrows.

## Real data and limits

Existing backend handlers remain authoritative. Targeting adds a natural instrument name to otherwise unscoped text or speech; this is a parser hint, not server-side target enforcement. Humming selects melody, clapping selects drums. Keyboard, connected Web MIDI Note-on and pads provide local short-note audition only. Microphone, files and export reuse existing handlers.

Scope uses actual Web Audio. Recording light uses the real capture state. Inspector shows real trace and scheduled notes. Release mode hides Inspector UI only.

## Verification

Verified drum selection plus a short density command produces an instrument-scoped request, real 92 BPM complete-band startup, live command scheduling, C major and C-G-Am-F state, a 12-note output bar, all Inspector views, optional keyboard audition, regrouped settings and responsive page width. Browser reported no script errors during these checks. Physical microphone recording and external MIDI were not hardware-tested. See docs/RENDERING_POC.md for frame, loading and audio measurements; the default is the lightweight static rendering.


## Inspector refinement

Compact unboxed transport and detached circular recording control replace the wide control slabs. Input uses a lightweight single field. Popover bounds respect the parent dialog. Inspector offers a stage rail with summary, raw payload and actual decision-to-playback state differences. Source JSON is navigated as files in one viewport; empty backend fields remain absent. See docs/RENDERING_POC.md for validation and interpretation limits.
