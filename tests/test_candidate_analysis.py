from copy import deepcopy
import uuid

import pytest
from fastapi.testclient import TestClient

from saia import candidate_analysis as notes
from saia.api import app


def card():
    return {"candidate_id": 7, "composition_sha256": "a" * 64, "label": "Graph method",
            "evidence": [{"title": "A graph method", "published_at": "2024-01-01",
                          "sources": [{"type": "arxiv", "url": "https://arxiv.org/abs/1609.02907"}]}]}


def body():
    return {"author": "Скаут", "summary": "", "pestle": [{"dimension": "technological",
            "text": "Метод может изменить обработку графов.", "effect": "opportunity",
            "horizon": "not_assessed", "dependencies": "Нужна проверка на независимых данных.",
            "evidence_ids": []}], "industry_impacts": []}


def binding():
    return notes.scope("m", 12, 7, card())


def test_notes_are_hypotheses_with_or_without_source_not_truth():
    result = notes.normalize_content(body(), binding(), card())
    assert result["pestle"][0]["status"] == "hypothesis_without_sources"
    value = body()
    value["pestle"][0]["evidence_ids"] = [notes.publication_references(card())[0]["id"]]
    result = notes.normalize_content(value, binding(), card())
    assert result["pestle"][0]["status"] == "evidence_linked_hypothesis"
    assert result["references"][0]["expert_validated"] is False
    assert result["references"][0]["source_connection_verified"] is True


@pytest.mark.parametrize("mutation", ["unknown_dimension", "array_dimension", "unknown_effect", "array_effect",
    "unknown_horizon", "array_horizon", "foreign_ref", "duplicate_ref", "bool_ref", "empty_text", "oversize_text",
    "nul_author", "empty_author", "empty_note", "extra_verified", "duplicate_dimension", "too_many_impacts", "extra_claim"])
def test_invalid_notes_do_not_become_saved_analysis(mutation):
    value = body()
    row = value["pestle"][0]
    if mutation == "unknown_dimension": row["dimension"] = "market_score"
    elif mutation == "array_dimension": row["dimension"] = []
    elif mutation == "unknown_effect": row["effect"] = "confirmed"
    elif mutation == "array_effect": row["effect"] = []
    elif mutation == "unknown_horizon": row["horizon"] = "tomorrow"
    elif mutation == "array_horizon": row["horizon"] = []
    elif mutation == "foreign_ref": row["evidence_ids"] = ["publication:" + "f" * 64]
    elif mutation == "duplicate_ref": row["evidence_ids"] = ["x", "x"]
    elif mutation == "bool_ref": row["evidence_ids"] = [True]
    elif mutation == "empty_text": row["text"] = ""
    elif mutation == "oversize_text": row["text"] = "я" * 1801
    elif mutation == "nul_author": value["author"] = "Скаут\x00"
    elif mutation == "empty_author": value["author"] = " "
    elif mutation == "empty_note": value["pestle"] = []
    elif mutation == "extra_verified": value["expert_validated"] = True
    elif mutation == "duplicate_dimension": value["pestle"].append(deepcopy(row))
    elif mutation == "too_many_impacts": value["industry_impacts"] = [{}] * 9
    else: row["confidence"] = 90
    with pytest.raises(ValueError):
        notes.normalize_content(value, binding(), card())


def test_industry_mechanism_and_conditions_are_independent_of_pestle():
    value = body()
    row = value["pestle"].pop()
    row.pop("dimension")
    row["industry"] = "Логистика"
    value["industry_impacts"] = [row]
    result = notes.normalize_content(value, binding(), card())
    assert not result["pestle"]
    assert result["industry_impacts"][0]["industry"] == "Логистика"
    assert result["industry_impacts"][0]["dependencies"]


def test_binding_detects_changed_source_text_metadata_even_with_same_work_ids():
    changed = card()
    changed["evidence"][0]["title"] = "Another title"
    new = notes.scope("m", 12, 7, changed)
    assert new["composition_sha256"] == binding()["composition_sha256"]
    assert new["evidence_sha256"] != binding()["evidence_sha256"]


def test_external_reference_must_match_card_or_explicit_scout_link(monkeypatch):
    identifier = str(uuid.uuid4())
    record = {"url": "https://news.mit.edu/test", "title": "Test news", "published_at": "2026-09-28"}
    saved = {"observation_id": identifier, "source": "mit_news_rss", "status": "complete", "created_at": "2026-09-28T00:00:00+00:00",
             "report_payload_sha256": "b" * 64, "payload": {"candidate_context_binding": binding(), "observations": [record]}}
    monkeypatch.setattr(notes.external_evidence_store, "read", lambda _: deepcopy(saved))
    ref = notes.external_reference(saved, record)
    assert notes.resolve_reference(ref["id"], binding(), card())["expert_validated"] is False
    saved["payload"]["candidate_context_binding"]["candidate_id"] = 99
    monkeypatch.setattr(notes.candidate_external_links, "for_candidate", lambda *a: {"links": []})
    with pytest.raises(ValueError, match="не относится"):
        notes.resolve_reference(ref["id"], binding(), card())
    monkeypatch.setattr(notes.candidate_external_links, "for_candidate", lambda *a: {"links": [{"observation_id": identifier, "record_url": record["url"]}]})
    assert notes.resolve_reference(ref["id"], binding(), card())["observation_id"] == identifier


def test_unsupported_external_status_and_record_rewrite_are_rejected(monkeypatch):
    identifier = str(uuid.uuid4())
    saved = {"observation_id": identifier, "source": "mit_news_rss", "status": "rate_limited", "payload": {}}
    monkeypatch.setattr(notes.external_evidence_store, "read", lambda _: saved)
    ref_id = f"external:{identifier}:" + "f" * 64
    with pytest.raises(ValueError, match="успешно"):
        notes.resolve_reference(ref_id, binding(), card())
    saved.update(status="complete", payload={"candidate_context_binding": binding(), "observations": []})
    with pytest.raises(ValueError, match="единственная"):
        notes.resolve_reference(ref_id, binding(), card())


def test_note_api_revision_conflict_is_not_silently_overwritten(monkeypatch):
    client = TestClient(app)
    calls = []
    monkeypatch.setattr(notes, "save", lambda *a: calls.append(a) or {"note": {"revision": 1}, "scientific_results_modified": False})
    response = client.post('/signals/m/7/analysis-note?score_run_id=12', json={**body(), "expected_revision": 0})
    assert response.status_code == 200
    assert calls[0][0:3] == ("m", 12, 7) and calls[0][4] == 0
    assert response.json()["scientific_results_modified"] is False
    def conflict(*a): raise notes.RevisionConflict("Заметка изменена")
    monkeypatch.setattr(notes, "save", conflict)
    assert client.post('/signals/m/7/analysis-note?score_run_id=12', json={**body(), "expected_revision": 0}).status_code == 409
    assert client.post('/signals/m/7/analysis-note?score_run_id=12', json={**body(), "expected_revision": True}).status_code == 422
    assert client.post('/signals/m/7/analysis-note?score_run_id=12', json={**body(), "expected_revision": 0, "scientific_results_modified": True}).status_code == 422


def test_note_integrity_check_rejects_tampering():
    payload = {"binding": binding(), "revision": 1, "content": body()}
    payload["report_sha256"] = notes.digest(payload)
    assert notes.checked_payload(payload, payload["report_sha256"], binding()) == payload
    payload["revision"] = 2
    with pytest.raises(ValueError, match="контрольной суммой"):
        notes.checked_payload(payload, payload["report_sha256"], binding())
