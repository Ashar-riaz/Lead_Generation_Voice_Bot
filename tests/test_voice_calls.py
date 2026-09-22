"""Voice integration tests. All Twilio calls and AI replies are simulated."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

import pytest
import requests
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.main import create_app
from app.store import StoreError
from app.twilio_voice_service import TwilioVoiceService, VoiceSettings
from config.settings import Settings
from tests.test_api import seed

SID = "AC" + "a" * 32
TOKEN = "test-twilio-auth-token"
FROM = "+442079460000"
TO = "+442079460001"
PUBLIC = "https://voice.example.com"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for name, value in {"TWILIO_ACCOUNT_SID": SID, "TWILIO_AUTH_TOKEN": TOKEN,
        "TWILIO_PHONE_NUMBER": FROM, "PUBLIC_BASE_URL": PUBLIC}.items():
        monkeypatch.setenv(name, value)
    settings = replace(Settings(), root_dir=tmp_path, database_path=tmp_path / "voice.sqlite3",
                       api_key="voice-test-key", llm_provider="gemini", gemini_api_key="not-a-real-key")
    app = create_app(settings)
    transport = MagicMock()
    count = iter(range(1, 100))
    transport.make_outbound_call.side_effect = lambda *args: SimpleNamespace(sid="CA" + f"{next(count):032x}")
    app.state.voice.transport_factory = lambda: transport
    app.state.voice.agent.generate = MagicMock(return_value={"reply": "Which team skills would you like to improve?", "end_call": False, "opt_out": False})
    app.state.voice.analysis.generate = MagicMock(return_value={"interest": "unclear", "confidence": "low",
        "summary": "The person discussed training.", "reasoning": "No clear decision was captured.",
        "evidence": [], "training_needs": [], "objections": [], "follow_up": "Review the transcript."})
    with TestClient(app, headers={"X-API-Key": settings.api_key}) as client:
        run = seed(app.state.store, count=1)
        yield client, app, transport, run


def prepare(workspace, **overrides):
    client, _, _, run = workspace
    result = client.post("/api/v1/voice/plans", json={"lead_id": run["leads"][0]["id"],
        "to_number": TO, "contact_name": "Alex", "purpose": "Discuss practical AI training and ask whether a follow-up would help.", **overrides})
    assert result.status_code == 201, result.text
    return result.json()


def approval(client, row, ready=True):
    result = client.post(f'/api/v1/voice/calls/{row["id"]}/approval', json={"expected_version": row["version"], "ready_to_call": ready})
    assert result.status_code == 200, result.text
    return result.json()


def start(client, row):
    return client.post(f'/api/v1/voice/calls/{row["id"]}/start', json={"expected_version": row["version"]})


def placed(workspace):
    client, *_ = workspace
    return start(client, approval(client, prepare(workspace))).json()


def webhook(workspace, row, action, form=None, turn=None, valid=True):
    client, _, _, _ = workspace
    path = f'/voice/{action}/{row["id"]}' + (f"?turn={turn}" if turn is not None else "")
    values = {"AccountSid": SID, "CallSid": row["call_sid"], "From": FROM, "To": TO, **(form or {})}
    signature = RequestValidator(TOKEN).compute_signature(PUBLIC + path, values) if valid else "bad"
    return client.post(path, data=values, headers={"X-Twilio-Signature": signature, "X-API-Key": "", "X-Forwarded-Host": "untrusted.example"})


def test_prepare_and_approval_do_not_call_and_start_is_idempotent(workspace):
    client, app, transport, _ = workspace
    plan = prepare(workspace)
    assert plan["status"] == "draft" and not plan["ready_to_call"]
    assert start(client, plan).status_code == 409
    saved = app.state.voice_store.get_call_context(plan["id"])
    assert 'Workforce Training' in saved['context']['wtd_profile']
    assert saved['context']['prospect']['company_name'] == 'Example Company 0'
    assert saved['context']['call_purpose'] == plan['purpose']
    assert saved['context']['programme_catalogue']
    approved = approval(client, plan)
    transport.make_outbound_call.assert_not_called()
    assert start(client, plan).status_code == 409  # stale version
    result = start(client, approved)
    assert result.status_code == 200 and result.json()['status'] == 'queued'
    assert start(client, approved).json()['id'] == result.json()['id']
    assert transport.make_outbound_call.call_count == 1


def test_api_key_and_boolean_approval_are_enforced(workspace):
    client, _, transport, _ = workspace
    assert client.get('/api/v1/voice/settings', headers={'X-API-Key':''}).status_code == 401
    plan = prepare(workspace)
    for value in ['true', 1]:
        assert client.post(f'/api/v1/voice/calls/{plan["id"]}/approval', json={'expected_version':1,'ready_to_call':value}).status_code == 422
    approved = approval(client, plan)
    revoked = approval(client, approved, False)
    assert start(client, revoked).status_code == 409
    transport.make_outbound_call.assert_not_called()


def test_missing_phone_and_sample_leads_are_rejected(workspace):
    client, app, transport, run = workspace
    for phone in ['', '02079460001', '+44999']:
        assert client.post('/api/v1/voice/plans',json={'lead_id':run['leads'][0]['id'],'to_number':phone,'purpose':'Training discussion'}).status_code == 422
    sample = seed(app.state.store, count=1, mock=True)
    assert client.post('/api/v1/voice/plans',json={'lead_id':sample['leads'][0]['id'],'to_number':TO,'purpose':'Training discussion'}).status_code == 409
    transport.make_outbound_call.assert_not_called()


def test_webhooks_require_signature_account_and_exact_call_binding(workspace):
    row = placed(workspace)
    for form, valid in [({},False),({'AccountSid':'AC'+'b'*32},True),({'To':'+442079460002'},True),({'CallSid':'CA'+'b'*32},True)]:
        assert webhook(workspace,row,'outbound-answer',form,valid=valid).status_code == 403
    assert workspace[1].state.voice_store.transcript(row['id']) == []


def test_greeting_is_immediate_british_and_replies_use_saved_context(workspace):
    _, app, _, _ = workspace
    row = placed(workspace)
    result = webhook(workspace,row,'outbound-answer')
    assert result.status_code == 200
    root = ET.fromstring(result.text)
    say = root.find('Gather/Say')
    assert say.attrib == {'voice':'Polly.Amy','language':'en-GB'}
    assert 'automated training assistant' in say.text and 'Example Company 0' in say.text
    assert root.find('Gather').attrib['language'] == 'en-GB'
    assert root.find('Gather').attrib['action'].startswith(PUBLIC+'/voice/gather/')
    app.state.voice.agent.generate.assert_not_called()
    reply = webhook(workspace,row,'gather',{'SpeechResult':'Yes, tell me about AI training.'},turn=1)
    assert 'Which team skills' in reply.text
    context, history, heard = app.state.voice.agent.generate.call_args.args
    assert context['contact_name'] == 'Alex' and 'follow-up' in context['call_purpose']
    assert history[0]['turn'] == 0 and 'AI training' in heard
    # Same Twilio turn retry returns stored TwiML, even after reopening the context service.
    app.state.voice_store.initialize()
    assert webhook(workspace,row,'gather',{'SpeechResult':'Yes, tell me about AI training.'},turn=1).text == reply.text
    assert app.state.voice.agent.generate.call_count == 1
    assert len(app.state.voice_store.transcript(row['id'])) == 2


def test_opt_out_hangs_up_and_blocks_later_calls(workspace):
    row = placed(workspace)
    webhook(workspace,row,'outbound-answer')
    result = webhook(workspace,row,'gather',{'SpeechResult':"Please don't call this number again"},turn=1)
    assert ET.fromstring(result.text).find('Hangup') is not None
    client, app, _, run = workspace
    assert client.post('/api/v1/voice/plans',json={'lead_id':run['leads'][0]['id'],'to_number':TO,'purpose':'Training discussion'}).status_code == 409
    app.state.voice.agent.generate.assert_not_called()


def test_silence_and_model_failure_produce_spoken_hangup(workspace):
    _, app, _, _ = workspace
    row = placed(workspace)
    webhook(workspace,row,'outbound-answer')
    first = webhook(workspace,row,'gather',turn=1)
    assert ET.fromstring(first.text).find('Gather') is not None
    second = webhook(workspace,row,'gather',turn=2)
    assert ET.fromstring(second.text).find('Say') is not None
    assert ET.fromstring(second.text).find('Hangup') is not None
    webhook(workspace,row,'status',{'CallStatus':'completed','SequenceNumber':'4'})
    row2 = placed(workspace)
    webhook(workspace,row2,'outbound-answer')
    app.state.voice.agent.generate.side_effect = TimeoutError('secret provider failure')
    result = webhook(workspace,row2,'gather',{'SpeechResult':'Can you explain your programmes?'},turn=1)
    assert 'technical problem' in result.text and 'secret provider failure' not in result.text
    assert ET.fromstring(result.text).find('Hangup') is not None


def test_status_callbacks_cannot_go_backwards(workspace):
    _, app, _, _ = workspace
    row = placed(workspace)
    assert webhook(workspace,row,'status',{'CallStatus':'ringing','SequenceNumber':'1'}).status_code == 204
    webhook(workspace,row,'outbound-answer')
    webhook(workspace,row,'status',{'CallStatus':'ringing','SequenceNumber':'2'})
    assert app.state.voice_store.get_call_context(row['id'])['status'] == 'in-progress'
    webhook(workspace,row,'status',{'CallStatus':'completed','SequenceNumber':'4'})
    webhook(workspace,row,'status',{'CallStatus':'in-progress','SequenceNumber':'3'})
    assert app.state.voice_store.get_call_context(row['id'])['status'] == 'completed'


def test_lost_ack_blocks_redial_until_manually_verified(workspace):
    client, app, transport, _ = workspace
    transport.make_outbound_call.side_effect = requests.ReadTimeout('private provider detail')
    plan = approval(client,prepare(workspace))
    row = start(client,plan).json()
    assert row['status'] == 'unknown' and 'private provider detail' not in json.dumps(row)
    start(client,plan)
    assert transport.make_outbound_call.call_count == 1
    another = approval(client,prepare(workspace))
    assert start(client,another).status_code == 409
    assert client.post(f'/api/v1/voice/calls/{row["id"]}/resolve',json={'verified_not_placed':False}).status_code == 422
    assert client.post(f'/api/v1/voice/calls/{row["id"]}/resolve',json={'verified_not_placed':True}).json()['status'] == 'not-placed'


def test_daily_limit_and_missing_model_block_paid_call(workspace):
    client, app, transport, _ = workspace
    plan = approval(client,prepare(workspace))
    app.state.voice.config = replace(app.state.voice.config,daily_limit=0)
    assert start(client,plan).status_code == 429
    app.state.voice.config = replace(app.state.voice.config,daily_limit=20)
    app.state.settings.gemini_api_key = ''
    assert start(client,plan).status_code == 503
    transport.make_outbound_call.assert_not_called()


def test_twilio_transport_creates_correct_callbacks_without_recording(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr('app.twilio_voice_service.Client',lambda *args,**kwargs:fake)
    config = VoiceSettings(SID,TOKEN,FROM,PUBLIC)
    service = TwilioVoiceService(config)
    call_id='1'*32
    service.make_outbound_call(TO,call_id)
    kwargs=fake.calls.create.call_args.kwargs
    assert kwargs['to']==TO and kwargs['from_']==FROM and kwargs['record'] is False
    assert kwargs['url']==PUBLIC+'/voice/outbound-answer/'+call_id
    assert kwargs['status_callback']==PUBLIC+'/voice/status/'+call_id
    assert kwargs['time_limit']==300 and kwargs['method']=='POST'


def test_openapi_voice_calls_do_not_require_microsoft(workspace):
    schema=workspace[1].openapi()
    assert schema['paths']['/api/v1/voice/calls/{call_id}/resolve']['post']['security']==[{'BackendKey':[]}]
    assert 'X-Session-Token' not in schema['paths']['/api/v1/voice/calls/{call_id}/approval']['post'].get('description','')


def test_sync_uses_twilio_sdk_sender_field(workspace):
    from twilio.rest.api.v2010.account.call import CallInstance
    client, _, transport, _ = workspace
    row = placed(workspace)
    transport.fetch_call.return_value = CallInstance(None, {'sid':row['call_sid'], 'to':TO, 'from':FROM, 'status':'completed'}, SID)
    response = client.post(f'/api/v1/voice/calls/{row["id"]}/sync')
    assert response.status_code == 200 and response.json()['status']=='completed'
    assert transport.make_outbound_call.call_count == 1


def test_opt_out_is_recorded_even_at_conversation_limit(workspace):
    _, app, _, _ = workspace
    row = placed(workspace)
    app.state.voice.config = replace(app.state.voice.config,max_turns=2)
    webhook(workspace,row,'outbound-answer')
    webhook(workspace,row,'gather',{'SpeechResult':'Tell me more.'},turn=1)
    result = webhook(workspace,row,'gather',{'SpeechResult':'Stop calling this number.'},turn=2)
    assert 'do-not-call' in result.text
    with app.state.store.connection() as db:
        assert db.execute('SELECT phone FROM voice_suppression').fetchone()[0]==TO