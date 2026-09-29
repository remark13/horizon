import copy
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from saia import result_export as export, source_context as context
from saia.api import app
from saia.hybrid import digest


def packet():
    return {"mission_id": "m", "score_run_id": 12, "cards": [
        {"candidate_id": 7, "composition_sha256": "a" * 64, "label": "Робототехника", "metrics": {"growth": None}},
        {"candidate_id": 8, "composition_sha256": "b" * 64, "label": "Materials"}], "provenance": [{"methodology_hash": "saved-hash"}]}


def setup(monkeypatch):
    original = packet()
    monkeypatch.setattr(export.candidates, "export_cards", lambda *args: original)
    monkeypatch.setattr(export.triage, "build_queue", lambda p, limit: {"queue": [{"card": c, "rank": i} for i, c in enumerate(p["cards"], 1)], "ranking_policy": {"kind": "saved"}})
    monkeypatch.setattr(export.source_context, "read_saved_all", lambda *args: {"contexts_truncated": False, "saved_query_bindings": [{"query": "query changed yesterday"}], "reports": [{"source": "event_registry", "status": "credentials_required", "observed_count": None}], "network_requested": False})
    monkeypatch.setattr(export.card_presentation, "read", lambda *args: {"presentation": None})
    monkeypatch.setattr(export.candidate_analysis, "read", lambda *args: {"note": None})
    monkeypatch.setattr(export.score_expert, "history", lambda *args: {"reviews": []})
    monkeypatch.setattr(export.candidate_external_links, "for_candidate", lambda *args, **kwargs: {"links": []})
    return original


@pytest.mark.parametrize("value", ["", "1,1", "-1", "0", "1, 2", "abc", "1," + ",".join(str(i) for i in range(2, 102)), "9999999999999", "１"])
def test_export_selection_is_bounded_and_exact(value):
    with pytest.raises(ValueError):
        export.parse_ids(value)


def test_export_uses_server_contexts_retains_nulls_and_does_not_change_original(monkeypatch):
    original = setup(monkeypatch)
    before = copy.deepcopy(original)
    report = export.build("m", 12, [7])
    assert report["scope"]["exported_candidate_ids"] == [7]
    assert report["scope"]["complete_score_run"] is False
    assert report["scientific_results"]["cards"][0]["metrics"]["growth"] is None
    assert "query changed yesterday" in json.dumps(report)
    assert report["network_requested"] is False and report["model_generation_requested"] is False
    assert report["expert_validation_required"] is False and report["scientific_results_modified"] is False
    assert original == before
    assert report["report_payload_sha256"] == digest({k: v for k, v in report.items() if k != "report_payload_sha256"})


def test_unknown_card_and_wrong_score_are_rejected(monkeypatch):
    setup(monkeypatch)
    with pytest.raises(ValueError):
        export.build("m", 12, [999])
    with pytest.raises(ValueError):
        export.build("m", 13, [7])
    with pytest.raises(ValueError):
        export.build("m", True, [7])


def test_public_only_and_mixed_export_keep_distinct_identities_and_scope(monkeypatch):
    setup(monkeypatch)
    from saia import scout_public_signals as public, public_signal_reviews
    refs = public.match('drone', included_phrases=[])
    row = refs['records'][0]
    monkeypatch.setattr(public, 'for_packet', lambda packet: refs)
    monkeypatch.setattr(public_signal_reviews, 'history_for_reference', lambda *args: {'reviews': []})
    only = export.build('m', 12, [], public_signal_ids=[row['id']])
    assert only['scientific_results']['cards'] == [] and only['ranked_candidates'] == []
    assert only['public_signals']['records'][0]['scientific_score'] is None
    assert only['scope']['total_exported_items'] == 1
    assert only['ranked_result_items'][0]['item_kind'] == 'public_signal'
    assert only['report_payload_sha256'] == digest({key: value for key, value in only.items() if key != 'report_payload_sha256'})
    mixed = export.build('m', 12, [7], public_signal_ids=[row['id']])
    assert [item['item_kind'] for item in mixed['ranked_result_items']] == ['saia_candidate', 'public_signal']
    client = TestClient(app)
    result = client.get(f'/results/m/export?score_run_id=12&include_scientific=false&public_signal_ids={row["id"]}')
    assert result.status_code == 200 and result.json()['scope']['exported_count'] == 0
    assert client.get('/results/m/export?score_run_id=12&include_scientific=false').status_code == 422
    assert client.get(f'/results/m/export?score_run_id=12&include_scientific=false&candidate_ids=7&public_signal_ids={row["id"]}').status_code == 422


def test_merged_public_citation_is_preserved_when_only_scientific_card_selected(monkeypatch):
    original = setup(monkeypatch)
    from saia import scout_public_signals as public, public_signal_reviews
    refs = public.match('drone', included_phrases=[])
    original['cards'][0]['label'] = refs['records'][0]['title']
    monkeypatch.setattr(public, 'for_packet', lambda packet: refs)
    monkeypatch.setattr(public_signal_reviews, 'history_for_reference', lambda *args: {'reviews': []})
    report = export.build('m', 12, [7])
    assert len(report['public_signals']['records']) == 1
    assert len(report['ranked_result_items']) == 1
    assert report['ranked_result_items'][0]['published_references'][0]['source_url'].startswith('https://')


def test_download_is_utf8_attachment_and_server_side(monkeypatch):
    setup(monkeypatch)
    client = TestClient(app)
    result = client.get("/results/m/export?score_run_id=12&candidate_ids=7")
    assert result.status_code == 200 and "attachment" in result.headers["content-disposition"]
    assert result.headers["cache-control"] == "no-store"
    assert "Робототехника" in result.text
    assert result.json()["additional_card_data"][0]["source_context"]["reports"][0]["observed_count"] is None
    assert client.get("/results/m/export?score_run_id=12&candidate_ids=99").status_code == 422


def saved_row(source="dealroom_marketmaps", query="old query", checksum_ok=True):
    binding = context.binding("m", 12, 7, packet()["cards"][0], query)
    payload = {"source": source, "role": context.external_evidence_store.SOURCES[source], "topic_id": context.topic_key(binding),
               "status": "complete", "candidate_context_binding": binding, "observations": [],
               "scientific_score_modified": False, "missing_is_zero": False}
    checksum = digest(payload)
    payload["report_payload_sha256"] = checksum if checksum_ok else "broken"
    return ("id", source, payload["role"], payload["topic_id"], "complete", checksum, payload, datetime.now(timezone.utc))


def fake_database(monkeypatch, rows, calls):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, args): calls.append((query, args))
        def fetchall(self): return rows
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cursor(self): return Cursor()
    monkeypatch.setattr(context.db, "connect", Connection)


def test_saved_context_export_survives_query_and_date_changes(monkeypatch):
    calls = []
    fake_database(monkeypatch, [saved_row()], calls)
    report = context.read_saved_all("m", 12, 7, packet()["cards"][0])
    assert report["saved_query_bindings"][0]["query"] == "old query"
    assert calls[0][1] == ("m", "12", "7", "a" * 64)
    assert "DISTINCT ON" in calls[0][0] and "LIMIT 51" in calls[0][0]
    assert report["contexts_truncated"] is False and report["network_requested"] is False


def test_context_export_checks_integrity_and_reports_cap(monkeypatch):
    fake_database(monkeypatch, [saved_row(checksum_ok=False)], [])
    with pytest.raises(ValueError):
        context.read_saved_all("m", 12, 7, packet()["cards"][0])
    fake_database(monkeypatch, [saved_row(query="query " + str(i)) for i in range(51)], [])
    assert context.read_saved_all("m", 12, 7, packet()["cards"][0])["contexts_truncated"] is True
