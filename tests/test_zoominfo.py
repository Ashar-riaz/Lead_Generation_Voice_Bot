"""Provider-boundary tests: simulated ZoomInfo HTTP only, no credentials or credits."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from tests.microsoft_helpers import microsoft_client
from app.schemas import RunCreate
from config.settings import Settings
from src.zoominfo.auth import TokenManager, ZoomInfoAuthError, shared_token_manager
from src.zoominfo.client import ZoomInfoClient, ZoomInfoError


def response(data=None, status=200, headers=None):
    value = MagicMock(status_code=status, headers=headers or {}, content=b"json", text="SENSITIVE RESPONSE MUST NOT BE EXPOSED")
    value.json.return_value = data if data is not None else {}
    return value


@pytest.fixture
def config(tmp_path):
    return replace(Settings(), zoominfo_client_id=f"test-app-{tmp_path.name}", zoominfo_client_secret="test-secret",
                   zoominfo_scope="", cache_dir=tmp_path / "cache", database_path=tmp_path / "test.sqlite3",
                   output_dir=tmp_path / "outputs", api_key="test-api-key", llm_provider="none")


@pytest.mark.parametrize("lifetime", [30, 1000, 86400])
def test_provider_lifetime_drives_renewal_without_daily_secret_change(monkeypatch, lifetime):
    clock = [100.0]
    monkeypatch.setattr("src.zoominfo.auth.time.monotonic", lambda: clock[0])
    post = MagicMock(side_effect=[response({"access_token": "first", "expires_in": lifetime}),
                                  response({"access_token": "second", "expires_in": lifetime})])
    monkeypatch.setattr("src.zoominfo.auth.requests.post", post)
    manager = TokenManager("app", "secret", "https://example.test/token")
    assert manager.get_token() == manager.get_token() == "first"
    assert post.call_count == 1
    clock[0] += lifetime - min(60, lifetime * .1) + .01
    assert manager.get_token() == "second"
    assert post.call_count == 2
    for call in post.call_args_list:
        assert call.kwargs["auth"] == ("app", "secret")
        assert call.kwargs["data"] == {"grant_type": "client_credentials"}
    assert "access_token" not in manager.status()


def test_concurrent_clients_reuse_token_and_ignore_a_delayed_401(config, monkeypatch):
    post = MagicMock(side_effect=[response({"access_token": "old", "expires_in": 1000}),
                                  response({"access_token": "fresh", "expires_in": 1000})])
    monkeypatch.setattr("src.zoominfo.auth.requests.post", post)
    clients = [ZoomInfoClient(config) for _ in range(8)]
    assert all(client.tokens is clients[0].tokens for client in clients)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda client: client.tokens.get_token(), clients)) == ["old"] * 8
    assert post.call_count == 1
    manager = clients[0].tokens
    manager.invalidate("old")
    assert manager.get_token() == "fresh"
    manager.invalidate("old")
    assert manager.get_token() == "fresh" and post.call_count == 2


@pytest.mark.parametrize("payload", [{}, {"access_token": "x", "expires_in": 0}, {"access_token": "x", "expires_in": "nan"},
                                     {"access_token": "", "expires_in": 1000}, {"access_token": "x", "expires_in": True}])
def test_malformed_token_responses_fail_closed(monkeypatch, payload):
    monkeypatch.setattr("src.zoominfo.auth.requests.post", lambda *a, **k: response(payload))
    with pytest.raises(ZoomInfoAuthError, match="invalid token response"):
        TokenManager("id", "secret", "https://example.test/token").get_token()


def test_oauth_uses_optional_scopes_and_redacts_rejection(monkeypatch):
    post = MagicMock(return_value=response(status=401))
    monkeypatch.setattr("src.zoominfo.auth.requests.post", post)
    with pytest.raises(ZoomInfoAuthError) as error:
        TokenManager("id", "test-secret", "https://example.test/token", scope="api:data:company").get_token()
    assert "Client Credentials" in str(error.value)
    assert "test-secret" not in str(error.value) and "SENSITIVE" not in str(error.value)
    assert post.call_args.kwargs["data"]["scope"] == "api:data:company"


def test_401_renews_once_even_after_a_rate_limit_retry(config, monkeypatch):
    client = ZoomInfoClient(config, max_retries=1)
    client.tokens = MagicMock()
    client.tokens.get_token.side_effect = ["old", "old", "fresh"]
    client.session.request = MagicMock(side_effect=[response(status=429, headers={"Retry-After": "0"}),
                                                    response(status=401), response({"data": []})])
    sleep = MagicMock()
    monkeypatch.setattr("src.zoominfo.client.time.sleep", sleep)
    assert client._request("GET", "/test") == {"data": []}
    client.tokens.invalidate.assert_called_once_with("old")
    assert client.session.request.call_count == 3
    assert client.session.request.call_args.kwargs["headers"]["Authorization"] == "Bearer fresh"


def test_401_retry_is_bounded_and_independent_of_transient_retry_budget(config):
    client = ZoomInfoClient(config, max_retries=0)
    client.tokens = MagicMock()
    client.tokens.get_token.return_value = "rejected"
    client.session.request = MagicMock(return_value=response(status=401))
    with pytest.raises(ZoomInfoError, match="renewed access token"):
        client._request("GET", "/test")
    assert client.session.request.call_count == 2
    client.tokens.invalidate.assert_called_once()


def test_long_rate_limit_does_not_retry_early(config, monkeypatch):
    client = ZoomInfoClient(config)
    client.tokens = MagicMock()
    client.session.request = MagicMock(return_value=response(status=429, headers={"Retry-After": "3600"}))
    sleep = MagicMock()
    monkeypatch.setattr("src.zoominfo.client.time.sleep", sleep)
    with pytest.raises(ZoomInfoError, match="longer rate-limit wait"):
        client._request("GET", "/test")
    sleep.assert_not_called()
    assert client.session.request.call_count == 1


@pytest.mark.parametrize("values", [
    {"min_signal_score": 90, "max_signal_score": 70}, {"min_signal_score": 59},
    {"signal_start_date": "2026-09-16", "signal_end_date": "2026-09-01"},
    {"audience_strength_min": "A", "audience_strength_max": "C"}, {"intent_topics": [" "]},
    {"intent_topics": [str(i) for i in range(51)]}, {"mock": True, "industry_codes": "123"},
])
def test_invalid_or_unsupported_filter_combinations_are_rejected(values):
    with pytest.raises(ValidationError):
        RunCreate(query="AI training", **values)


def test_live_is_default_and_missing_credentials_never_generate_samples(config):
    settings = replace(config, zoominfo_client_id="", zoominfo_client_secret="")
    with microsoft_client(create_app(settings)) as client:
        assert client.post("/api/v1/runs", json={"query": "AI training"}).status_code == 503
        assert client.get("/api/v1/lookups/intent-topics").status_code == 503
        assert client.post("/api/v1/zoominfo/test-connection").status_code == 503
        assert client.get("/api/v1/runs").json()["total"] == 0
    assert RunCreate(query="AI training").mock is False


def test_live_http_flow_forwards_intent_company_location_filters_and_stores_evidence(config, monkeypatch):
    token = MagicMock(return_value=response({"access_token": "test-access", "expires_in": 1000, "token_type": "Bearer"}))
    monkeypatch.setattr("src.zoominfo.auth.requests.post", token)
    requests_seen = []
    def provider(session, method, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer test-access"
        requests_seen.append((method, url, kwargs))
        if url.endswith("/lookup/intent-topics"):
            return response({"data": [{"id": "topic-1", "attributes": {"name": "Cloud Applications"}}]})
        assert url.endswith("/data/v1/intent/search") and method == "POST"
        assert kwargs["params"] == {"page[number]": 1, "page[size]": 50, "sort": "-signalScore"}
        assert kwargs["json"] == {"data": {"type": "IntentSearch", "attributes": {
            "topics": ["Cloud Applications"], "signalScoreMin": 80, "signalScoreMax": 99,
            "findRecommendedContacts": True, "country": "United States", "state": "California",
            "metroRegion": "CA - San Francisco", "industryCodes": "123", "employeeCount": "100to249",
            "revenue": "1Mto5M", "techAttributeTagList": "456", "signalStartDate": "2026-09-01",
            "signalEndDate": "2026-09-15", "audienceStrengthMin": "C", "audienceStrengthMax": "A",
        }}}
        return response({"data": [{"type": "Intent", "attributes": {
            "company": {"id": 1001, "name": "Provider fixture company", "website": "example.com"},
            "topic": "Cloud Applications", "signalScore": 92, "audienceStrength": "B", "signalDate": "2026-09-12",
        }}]})
    monkeypatch.setattr("requests.Session.request", provider)
    transport = MagicMock()
    with microsoft_client(create_app(config, transport)) as client:
        created = client.post("/api/v1/runs", json={
            "query": "Cloud skills training", "intent_topics": ["Cloud Applications"], "country": "United States",
            "state": "California", "metro_region": "CA - San Francisco", "industry_codes": "123",
            "employee_count": "100to249", "revenue": "1Mto5M", "tech_products": "456",
            "min_signal_score": 80, "max_signal_score": 99, "signal_start_date": "2026-09-01",
            "signal_end_date": "2026-09-15", "audience_strength_min": "C", "audience_strength_max": "A",
            "find_buyers": False, "enrich": False, "write_emails": False,
        })
        assert created.status_code == 202, created.text
        run = client.get(f'/api/v1/runs/{created.json()["id"]}').json()
        assert run["status"] == "completed", run
        assert run["mock"] is False and len(run["leads"]) == 1
        assert run["leads"][0]["company_id"] == "1001"
        assert run["leads"][0]["signals"][0]["signal_date"] == "2026-09-12"
        assert run["result"]["plan"]["intent_filters"]["metroRegion"] == "CA - San Francisco"
        assert run["emails"] == []
        checked = client.post("/api/v1/zoominfo/test-connection")
        assert checked.status_code == 200
        assert checked.json()["automatic_renewal"] is True and checked.json()["intent_topic_count"] == 1
        assert "test-access" not in checked.text and "test-secret" not in checked.text
        assert client.post("/api/v1/zoominfo/test-connection", headers={"X-API-Key": "wrong"}).status_code == 401
    assert token.call_count == 1  # Search and connection check reuse the token.
    assert len(requests_seen) == 3  # Lookup, intent search, uncached connection-check lookup.
    transport.send.assert_not_called()


def test_rejected_live_auth_stays_failed_instead_of_falling_back(config, monkeypatch):
    monkeypatch.setattr("src.zoominfo.auth.requests.post", lambda *a, **k: response(status=401))
    transport = MagicMock()
    with microsoft_client(create_app(config, transport)) as client:
        created = client.post("/api/v1/runs", json={"query": "AI training"})
        run = client.get(f'/api/v1/runs/{created.json()["id"]}').json()
        assert run["status"] == "failed" and run["mock"] is False
        assert run["leads"] == run["emails"] == []
        assert "Client Credentials" in run["error"] and "SENSITIVE" not in run["error"]
        assert client.post("/api/v1/zoominfo/test-connection").status_code == 502
    transport.send.assert_not_called()


def test_unknown_topic_never_reaches_intent_search(config, monkeypatch):
    lookup = MagicMock(return_value=["Cloud Applications"])
    search = MagicMock()
    monkeypatch.setattr(ZoomInfoClient, "intent_topics", lookup)
    monkeypatch.setattr(ZoomInfoClient, "search_intent", search)
    with microsoft_client(create_app(config)) as client:
        created = client.post("/api/v1/runs", json={"query": "AI training", "intent_topics": ["made up topic"]})
        run = client.get(f'/api/v1/runs/{created.json()["id"]}').json()
        assert run["status"] == "failed" and "topics are unavailable" in run["error"]
    search.assert_not_called()


def test_lookup_cache_is_scoped_to_account_and_can_be_refreshed(config):
    a = ZoomInfoClient(config)
    b = ZoomInfoClient(replace(config, zoominfo_client_id="other-app"))
    a._request = MagicMock(return_value={"data": [{"attributes": {"name": "First account"}}]})
    b._request = MagicMock(return_value={"data": [{"attributes": {"name": "Second account"}}]})
    assert a.intent_topics() == ["First account"]
    assert b.intent_topics() == ["Second account"]
    a.intent_topics()
    assert a._request.call_count == 1
    a.lookup("intent-topics", use_cache=False)
    assert a._request.call_count == 2
    with pytest.raises(ValueError, match="Unsupported"):
        a.lookup("../../secret")


def test_intent_pagination_merges_companies_when_first_page_is_full(config):
    client = ZoomInfoClient(config)
    def row(topic):
        return {"attributes": {"company": {"id": "1001", "name": "Fixture"}, "topic": topic, "signalScore": 90}}
    client._request = MagicMock(side_effect=[{"data": [row("A")], "links": {"next": "/next"}},
                                           {"data": [row("B")], "links": {"next": None}}])
    leads = client.search_intent(["A", "B"], page_size=1)
    assert len(leads) == 1 and len(leads[0].signals) == 2
    assert client._request.call_count == 2
