from datetime import date
from unittest.mock import MagicMock

import pytest

from config.settings import Settings
from src.agents.email_agent import EmailAgent, tidy
from src.agents.query_agent import QueryAgent
from src.agents.scoring import score_lead
from src.knowledge.loader import load_knowledge
from src.models import Contact, IntentSignal, Lead, SearchPlan
from src.pipeline import LeadPipeline
from src.zoominfo.client import ZoomInfoClient
from src.zoominfo.mock_client import MockZoomInfoClient


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.llm_provider = "none"
    s.output_dir = tmp_path
    return s


@pytest.fixture
def no_llm():
    llm = MagicMock()
    llm.available = False
    return llm


@pytest.fixture
def kb(settings):
    return load_knowledge(settings.knowledge_dir)


def plan(key="ai"):
    return SearchPlan("q", key, "AI Automation", ["Artificial Intelligence"], ["HR Director"])


def test_query_agent_picks_ai_topics(kb, no_llm):
    topics = ["Artificial Intelligence", "Generative AI", "Cybersecurity", "Employee Training"]
    p = QueryAgent(kb, no_llm).plan("show me companies that need AI training", topics, "United Kingdom")
    assert p.category_key == "ai"
    assert "Artificial Intelligence" in p.intent_topics
    assert "Cybersecurity" not in p.intent_topics
    assert p.country == "United Kingdom"


def test_query_agent_cyber_and_hot(kb, no_llm):
    p = QueryAgent(kb, no_llm).plan("hot leads for cyber security training", ["Cybersecurity", "AI"], "")
    assert p.category_key == "cyber"
    assert p.min_signal_score == 85


def test_query_agent_rejects_unknown_topics(kb, no_llm):
    with pytest.raises(ValueError):
        QueryAgent(kb, no_llm).plan("AI training", ["Plumbing Supplies"], "")


def test_scoring_hot_lead():
    lead = Lead("1", "Acme",
                signals=[IntentSignal("AI", signal_score=95, audience_strength="A", signal_date="2026-09-10"),
                         IntentSignal("GenAI", signal_score=80)],
                contacts=[Contact("9", "Jo", "Bloggs", "Head of Learning and Development", email="jo@acme.example")])
    score_lead(lead, plan(), today=date(2026, 9, 15))
    assert lead.tier == "Hot"
    assert lead.score == 35 + 20 + 10 + 5 + 10 + 10


def test_scoring_cool_lead():
    lead = Lead("2", "Beta", signals=[IntentSignal("AI", signal_score=62, audience_strength="E")])
    score_lead(lead, plan(), today=date(2026, 9, 15))
    assert lead.tier == "Cool"


def test_tidy_keeps_testimonials_verbatim():
    quote = '"WTD adds real value — we highly recommend them!"'
    out = tidy(f"Great news — really! One client said {quote}")
    assert quote in out
    assert "Great news, really." in out


def test_email_template_and_programme_choice(kb, no_llm, settings):
    agent = EmailAgent(kb, no_llm, settings)
    senior = Lead("1", "Acme Ltd (SAMPLE)", signals=[IntentSignal("AI", 90)],
                  contacts=[Contact("9", "Jo", "Bloggs", "HR Director", email="jo@acme.example")])
    d = agent.write(senior, plan())
    assert d.subject and d.body.startswith("Hi Jo,")
    assert "AI Leadership" in d.programme
    assert "(SAMPLE)" not in d.subject
    assert "—" not in d.body and "no thanks" in d.body

    junior = Contact("8", "Sam", "Lee", "Operations Analyst")
    assert "Practitioner" in agent.pick_programme("ai", junior.job_title)["name"]


def test_full_mock_pipeline(settings, no_llm):
    result = LeadPipeline(settings, MockZoomInfoClient(settings), llm=no_llm).run("companies that need AI training", limit=5)
    names = [l.company_name for l in result.leads]
    assert "Harlow Data Services (SAMPLE)" not in names  # cyber/data only, no AI intent
    assert result.leads[0].company_name.startswith("Northbridge")
    assert result.drafts and all(d.to_email for d in result.drafts)
    assert (result.run_dir / "leads.csv").exists()
    assert (result.run_dir / "emails.csv").exists()


def test_intent_request_body_matches_zoominfo_spec(settings):
    settings.zoominfo_client_id = "id"
    settings.zoominfo_client_secret = "secret"
    client = ZoomInfoClient(settings)
    client._request = MagicMock(return_value={
        "data": [{"type": "Intent", "attributes": {
            "topic": "Artificial Intelligence", "signalScore": 88, "audienceStrength": "B",
            "signalDate": "2026-09-01T00:00:00Z",
            "company": {"id": "123", "name": "Acme", "website": "acme.example"},
            "recommendedContacts": [{"id": "55", "firstName": "Ann", "lastName": "Lee", "jobTitle": "CTO"}]}}],
        "meta": {"page": {"number": 1, "total": 1}},
    })
    leads = client.search_intent(["Artificial Intelligence"], country="United Kingdom", signal_score_min=70)

    method, path = client._request.call_args.args
    body = client._request.call_args.kwargs["body"]
    assert (method, path) == ("POST", "/data/v1/intent/search")
    assert body["data"]["type"] == "IntentSearch"
    assert body["data"]["attributes"]["topics"] == ["Artificial Intelligence"]
    assert body["data"]["attributes"]["signalScoreMin"] == 70
    assert leads[0].company_name == "Acme" and leads[0].contacts[0].job_title == "CTO"
    assert leads[0].top_signal.signal_date == "2026-09-01"


def test_llm_email_path_is_sanitised(kb, settings):
    fake = MagicMock()
    fake.available = True
    fake.generate_json.return_value = {
        "subject": "AI skills at Acme.",
        "body": "Hi Jo,\n\nQuick one — funded AI training is available!\n\nFancy a call?",
    }
    lead = Lead("1", "Acme Ltd", signals=[IntentSignal("Generative AI", 90)],
                contacts=[Contact("9", "Jo", "Bloggs", "HR Director", email="jo@acme.example")])
    d = EmailAgent(kb, fake, settings).write(lead, plan())

    prompt = fake.generate_json.call_args.args[1]
    assert "Workforce Training & Development" in prompt   # knowledge base is injected
    assert "Generative AI" in prompt and "Acme" in prompt
    assert d.subject == "AI skills at Acme"
    assert "—" not in d.body and "!" not in d.body.split("Best regards")[0]
    assert d.body.rstrip().endswith("contact you again.")


def test_client_retries_429_and_refreshes_401(settings, monkeypatch):
    settings.zoominfo_client_id = "id"
    settings.zoominfo_client_secret = "secret"
    client = ZoomInfoClient(settings)
    client.tokens.get_token = MagicMock(return_value="tok")
    client.tokens.invalidate = MagicMock()
    monkeypatch.setattr("src.zoominfo.client.time.sleep", lambda s: None)

    def resp(code, body=b"{}", headers=None):
        r = MagicMock(status_code=code, content=body, headers=headers or {})
        r.json.return_value = {"data": []}
        r.text = ""
        return r

    client.session.request = MagicMock(side_effect=[resp(401), resp(429, headers={"Retry-After": "0"}), resp(200)])
    assert client._request("POST", "/data/v1/intent/search", body={}) == {"data": []}
    client.tokens.invalidate.assert_called_once()
    headers = client.session.request.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Content-Type"] == "application/vnd.api+json"
