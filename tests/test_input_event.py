import copy
import pytest
from jammate.input_event import make_event, validate_event, from_melody_document, perceive_rhythm, to_legacy_rhythm
from test_rhythm_analysis import claps, SR


def test_hum_adapter_preserves_original_seconds_and_unknown_tempo():
    document={'source':{'durationSec':5},'selectedResult':'cleaned',
              'results':{'cleaned':{'notes':[{'pitchMidi':60,'startSec':.4,'durationSec':3.1}]}}}
    before=copy.deepcopy(document)
    event=from_melody_document(document,session_id='s',started_at=23)
    assert event['started_at']==23 and event['ended_at']==28
    assert event['payload']['notes'][0]['start_sec']==.4
    assert event['payload']['tempo_bpm'] is None and event['confidence'] is None
    assert document==before


def test_rhythm_uses_existing_backend_and_roundtrips_legacy_adapter():
    audio=claps([.3,.9,1.5,2.1,2.7,3.3,3.6,4.5],5.2)
    event=perceive_rhythm(audio,SR,session_id='s',started_at=10)
    assert event['event_type']=='rhythm_seed'
    assert event['ended_at']==pytest.approx(15.2)
    assert event['payload']['rhythm']['onset_beats']==(0,1,1.5,3)
    assert to_legacy_rhythm(event).context.onset_beats==(0,1,1.5,3)


@pytest.mark.parametrize('field,value',[('started_at',float('nan')),('ended_at',0),('confidence',2),('session_id',''),('source','unknown')])
def test_reject_invalid_envelopes(field,value):
    event=make_event('humming',{'notes':[]},3,session_id='s')
    event[field]=value
    with pytest.raises(ValueError):validate_event(event)


def test_reject_note_beyond_recording():
    with pytest.raises(ValueError,match='fit inside'):
        make_event('humming',{'notes':[{'pitch_midi':60,'start_sec':2.9,'duration_sec':.4}]},3,session_id='s')


def test_notes_only_export_is_accepted_without_results():
    document={'source':{'durationSec':2},'notes':[{'pitchMidi':60,'startSec':.1,'durationSec':1}]}
    event=from_melody_document(document,session_id='external-session',started_at=19)
    assert event['ended_at']==21 and event['payload']['notes'][0]['start_sec']==.1


@pytest.mark.parametrize('velocity',[-.1,1.1,True,'loud'])
def test_invalid_velocity_is_rejected(velocity):
    with pytest.raises(ValueError,match='velocity'):
        make_event('humming',{'notes':[{'pitch_midi':60,'start_sec':0,'duration_sec':1,'velocity':velocity}]},2,session_id='s')


def test_malformed_rhythm_is_a_validation_error():
    with pytest.raises(ValueError,match='rhythm'):
        make_event('rhythm',{},3,session_id='s')


def test_quantized_onsets_must_match_their_rhythm_clock():
    event=perceive_rhythm(claps([.3,.9,1.5,2.1,2.7,3.3,3.6,4.5],5.2),SR,session_id='s')
    event['payload']['onsets_sec'][0] += 1
    with pytest.raises(ValueError,match='onsets_sec'):
        validate_event(event)
