"""Ground-truth note fixtures for the preserve experiment.

Single source of truth: both the deterministic MIDI baseline (control) and the
MRT2 runs derive their input events from these definitions, so the input side
of every comparison is identical by construction.

Timing is in beats at BPM=96 (one beat = 0.625 s, one bar = 2.5 s).
"""
from jammate.contracts.adapters import from_text
from jammate.interaction import Motif, TransportPosition
from jammate.music import BEATS_PER_BAR, Note
from jammate.orchestration.plans import make_plan
from jammate.state import JamState

BPM = 96
SPB = 60.0 / BPM   # seconds per beat
BAR = BEATS_PER_BAR
DEMO_MOTIF = (Note(60, 0, .5), Note(64, .5, .5), Note(67, 1, 1), Note(64, 2, 1))
MOTIF_ID = 'demo-motif'
EVENT_ID = 'fixture'


def demo_state(notes=DEMO_MOTIF, *, bar=0, **settings):
    motif = (Motif(MOTIF_ID, 'user', (EVENT_ID,), notes),) if notes else ()
    defaults = dict(tempo_bpm=BPM, last_user_motif=notes, motifs=motif,
                    current_motif_id=MOTIF_ID if motif else None,
                    position=TransportPosition(bar=bar, next_bar=bar))
    return JamState(**(defaults | settings))


def demo_plan(notes=DEMO_MOTIF, *, bar=0, bars=8, preserve=('tempo', 'key')):
    state = demo_state(notes, bar=bar)
    event = from_text('回应我的旋律', session_id=state.session_id, event_id=EVENT_ID)
    actions = [dict(action='ECHO_MOTIF', scope='motif', strength=.7, parameters={'motif_id': MOTIF_ID})]
    return state, make_plan(state, (event,), 'mrt2-demo', 'RESPOND', actions, preserve,
                            {}, bars, 'Local demo fixture', [])


def _bar(bar_index, spec):
    return tuple(Note(p, bar_index * BAR + s, d) for p, s, d in spec)


# ---- Fixture A: monophonic C-E-G-E quarter-note melody, 0.25-beat gaps ----
# Gaps keep every onset isolated for reliable onset detection and pitch tracking.
A_MOTIF = ((60, 0, 0.75), (64, 1, 0.75), (67, 2, 0.75), (64, 3, 0.75))


def fixture_a(bars=8):
    return tuple(n for bar in range(bars) for n in _bar(bar, A_MOTIF))


# ---- Fixture B: C / Am / F / G chord blocks, 2 bars (8 beats) each ----
B_CHORDS = (
    ('C', (60, 64, 67)),
    ('Am', (57, 60, 64)),
    ('F', (53, 57, 60)),
    ('G', (55, 59, 62)),
)
B_SEGMENT_BARS = 2


def fixture_b():
    notes = []
    for i, (_, pitches) in enumerate(B_CHORDS):
        base = i * B_SEGMENT_BARS * BAR
        for p in pitches:
            notes.append(Note(p, base, B_SEGMENT_BARS * BAR, instrument='keys'))
    return tuple(notes)


# ---- Fixture C: 16th-note C-E-G-E pattern, straight vs 66% swing ----
# Swing shifts the second of each 16th pair by 1/12 beat = 52 ms at 96 BPM,
# which is above the adapter's 25 Hz (40 ms) quantization, so the information
# reaches the conditioning -- the question is whether it reaches the output.
C_PITCHES = (60, 64, 67, 64)
C_STRAIGHT = (0.0, 0.25, 0.5, 0.75)
C_SWING = (0.0, 1 / 3, 0.5, 5 / 6)


def fixture_c(swing=False, bars=8):
    positions = C_SWING if swing else C_STRAIGHT
    notes = []
    for bar in range(bars):
        for beat in range(BAR):
            base = bar * BAR + beat
            for k, pos in enumerate(positions):
                notes.append(Note(C_PITCHES[k], base + pos, 0.15))
    return tuple(notes)


# ---- Fixture D: fixture A content repeated for 60 s (BPM stability / drift) ----
def fixture_d():
    base = fixture_a()
    return tuple(
        n
        for shift_beats in (0, 32, 64)
        for n in (base if shift_beats == 0 else tuple(x.shifted(shift_beats) for x in base))
    )


FIXTURES = {
    'a': fixture_a,
    'b': fixture_b,
    'c-straight': fixture_c,
    'c-swing': lambda: fixture_c(swing=True),
    'd': fixture_d,
}
