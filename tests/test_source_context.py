import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from saia import source_context as context
from saia.api import app
from saia.hybrid import digest


def scope(query="autonomous flight"):
    return context.binding("m", 12, 7, {"composition_sha256": "a" * 64}, query, date(2026, 9, 28))


def saved(source="mit_news_rss", status="complete", rows=None):
    return {"source": source, "status": status, "observation_id": "123", "created_at": "2026-09-28T12:00:00+00:00",
            "payload": {"retrieved_at": "2026-09-28T12:00:00+00:00", "observations": rows}}


def test_default_query_uses_label_not_hidden_guess_and_scopes_are_exact():
    assert context.default_query({"label": "Autonomous flight — control"}) == "Autonomous flight control"
    assert context.default_query({"label": "Flight, obstacle, avoidance and autonomous"}) == "Flight obstacle avoidance autonomous"
    first = scope()
    second = {**first, "composition_sha256": "b" * 64}
    assert context.topic_key(first) != context.topic_key(second)
    assert first["changes_scientific_metrics"] is False


@pytest.mark.parametrize("text", ["x", "robot OR (patent)", "x\ny", "https://internal/", "a" * 161])
def test_context_rejects_unsafe_or_unbounded_query(text):
    with pytest.raises(ValueError):
        context.validate_query(text)


@pytest.mark.parametrize("sources", [[], ["unknown"], ["deps_dev"], ["gdelt_doc_2_0"] * 2, list(context.ADAPTERS)])
def test_context_source_set_is_bounded_and_not_arbitrary(sources):
    with pytest.raises(ValueError):
        context.validate_sources(sources)


def test_missing_response_is_not_zero_and_automatic_is_not_validated():
    result = context._packet(scope(), ["mit_news_rss", "epo_ops"], {
        "mit_news_rss": saved(rows=[{"url": "https://news.mit.edu/robot", "title": "Robot", "published_at": "2026-09-28", "language": "en"}]),
        "epo_ops": saved("epo_ops", "credentials_required"),
    })
    assert result["reports"][1]["observed_count"] is None
    record = result["reports"][0]["materials"][0]
    assert record["record_date"] == "2026-09-28"
    assert record["match_status"] == "automatic_search_match"
    assert record["expert_validated"] is False
    assert result["scientific_score_modified"] is False


def test_seen_date_is_not_called_publication_and_no_language_is_guessed():
    record = context.passport("gdelt_doc_2_0", {"url": "https://example.org/story", "title": "A story", "seen_date": "2026-09-28"}, saved())
    assert record["date_kind"] == "observed_by_aggregator"
    assert record["language_original"] is None
    assert context.passport("gdelt_doc_2_0", {"url": "javascript:alert(1)"}, saved()) is None
    assert context.passport("gdelt_doc_2_0", {"url": "https://:secret@example.org/story"}, saved()) is None


def test_grant_and_clinical_dates_keep_their_meaning():
    for source, key, kind in [("ukri_gtr", "project_start_date", "project_start"),
                              ("clinicaltrials_gov", "first_posted_date", "registry_first_posted")]:
        record = context.passport(source, {"url": "https://example.org/item", key: "2026-01-15"}, saved())
        assert (record["record_date"], record["date_kind"]) == ("2026-01-15", kind)


def test_context_cache_is_shorter_for_error_and_never_uses_future_date():
    now = datetime(2026, 9, 28, 13, tzinfo=timezone.utc)
    assert context._fresh(saved(), now)
    assert not context._fresh(saved(status="source_unavailable"), now)
    assert not context._fresh(saved(), now - timedelta(hours=2))


def test_read_packet_marks_expired_errors_for_automatic_refresh_not_empty_success():
    result = context._packet(scope(), ["mit_news_rss", "epo_ops"],
        {"mit_news_rss": saved(rows=[]), "epo_ops": saved("epo_ops", "credentials_required")},
        now=datetime(2026, 9, 28, 13, tzinfo=timezone.utc))
    assert result["reports"][0]["cache_fresh"] is True
    assert result["reports"][1]["cache_fresh"] is False
    assert result["reports"][1]["observed_count"] is None


def test_context_binding_is_required_when_loading_cached_observation(monkeypatch):
    monkeypatch.setattr(context.external_evidence_store, "history", lambda *args, **kw: {"observations": [{"observation_id": "1"}, {"observation_id": "2"}]})
    values = {"1": {"payload": {"candidate_context_binding": {**scope(), "candidate_id": 8}}},
              "2": {"payload": {"candidate_context_binding": scope()}}}
    monkeypatch.setattr(context.external_evidence_store, "read", values.__getitem__)
    assert context._latest(scope(), "mit_news_rss") == values["2"]


def test_adapter_exception_is_saved_as_unknown_with_binding(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("Unexpected response with sensitive details")
    fake = SimpleNamespace(NewsQuery=lambda **kw: kw, fetch=fail)
    monkeypatch.setattr(context.importlib, "import_module", lambda name: fake)
    monkeypatch.setattr(context.external_evidence_store, "record", lambda report: {"observation_id": "1", "status": report["status"]})
    result = context._fetch_one("gdelt_doc_2_0", scope())
    payload = result["payload"]
    assert payload["observations"] is None
    assert payload["candidate_context_binding"] == scope()
    assert "sensitive" not in str(payload)
    assert payload["report_payload_sha256"] == digest({key: val for key, val in payload.items() if key != "report_payload_sha256"})


def test_source_catalog_is_registered_and_does_not_expose_keys(monkeypatch):
    monkeypatch.setattr(context, "_last_observations", lambda: {})
    monkeypatch.setenv("EPO_OPS_CONSUMER_KEY", "private-key")
    monkeypatch.delenv("EPO_OPS_CONSUMER_SECRET", raising=False)
    result = context.catalog()
    assert len(result["sources"]) == 27 + result["publisher_channels"]
    assert result["publisher_channels"] >= 100
    assert "private-key" not in str(result)
    epo = next(row for row in result["sources"] if row["source"] == "epo_ops")
    assert epo["configuration_status"] == "credentials_required"
    assert result["investment_deals"]["status"] == "limited_unverified_sample"
    assert all(re.search(r"[А-Яа-я]", item["regional_coverage_ru"]) for item in result["sources"])
    assert all(re.search(r"[А-Яа-я]", item["use_policy_ru"]) for item in result["sources"])


def test_source_context_api_validates_and_passes_exact_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(context, "collect", lambda *args: calls.append(args) or {"scientific_score_modified": False})
    client = TestClient(app)
    result = client.post("/signals/m/7/source-context?score_run_id=12", json={"query": "autonomous flight", "sources": ["mit_news_rss"]})
    assert result.status_code == 200
    assert calls == [("m", 12, 7, "autonomous flight", ["mit_news_rss"])]
    assert client.post("/signals/m/7/source-context?score_run_id=12", json={"sources": list(context.ADAPTERS)}).status_code == 422
