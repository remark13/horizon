from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from saia import candidate_external_links as links
from saia.api import app


OBSERVATION_ID = str(uuid4())
RECORD_URL = "https://example.org/records/123"


@pytest.fixture
def saved_record(monkeypatch):
    monkeypatch.setattr(links.candidates, "export_cards", lambda mission, score: {
        "mission_id": mission, "score_run_id": score,
        "cards": [{"candidate_id": 7, "composition_sha256": "a" * 64}],
    })
    monkeypatch.setattr(links.external_evidence_store, "read", lambda identifier: {
        "observation_id": identifier, "source": "nasa_ntrs",
        "role": "bibliographic_enrichment_only", "status": "complete",
        "report_payload_sha256": "b" * 64,
        "payload": {"observations": [{"url": RECORD_URL, "title": "Specific record"}]},
    })


def test_prepare_anchors_exact_candidate_and_saved_record(saved_record):
    value = links.prepare("mission", 42, 7, OBSERVATION_ID, RECORD_URL,
                          "relevant_to_topic", "скаут",
                          "Запись прямо относится к выбранной тематической линии.")
    assert value["composition_sha256"] == "a" * 64
    assert value["record_snapshot"]["title"] == "Specific record"
    assert value["source"] == "nasa_ntrs"
    assert value["scientific_score_modified"] is False


def test_prepare_rejects_broad_observation_without_exact_record(saved_record):
    with pytest.raises(ValueError, match="не совпала"):
        links.prepare("mission", 42, 7, OBSERVATION_ID,
                      "https://example.org/records/other", "relevant_to_topic",
                      "скаут", "Совпадение названия не является доказательством связи.")


def test_prepare_rejects_other_run_candidate_and_incomplete_observation(saved_record, monkeypatch):
    with pytest.raises(ValueError, match="отсутствует"):
        links.prepare("mission", 42, 8, OBSERVATION_ID, RECORD_URL,
                      "relevant_to_topic", "скаут",
                      "Необходимо проверить принадлежность выбранному прогону.")
    monkeypatch.setattr(links.external_evidence_store, "read", lambda identifier: {
        "observation_id": identifier, "status": "rate_limited",
    })
    with pytest.raises(ValueError, match="успешно полученного"):
        links.prepare("mission", 42, 7, OBSERVATION_ID, RECORD_URL,
                      "relevant_to_topic", "скаут",
                      "Нельзя привязывать ответ с ошибкой от внешнего сервиса.")


def test_link_replay_accepts_database_uuid_value(saved_record, monkeypatch):
    reason = "Запись прямо относится к выбранной тематической линии."
    entry = links.prepare("mission", 42, 7, OBSERVATION_ID, RECORD_URL,
                          "relevant_to_topic", "скаут", reason)
    monkeypatch.setattr(links, "prepare", lambda *args: entry)
    link_id = str(uuid4())
    fields = ("mission_id", "score_run_id", "candidate_id", "composition_sha256",
              "observation_id", "observation_sha256", "source", "role",
              "record_url", "record_snapshot", "assessment", "linked_by",
              "rationale", "scientific_score_modified")
    row = [entry[field] for field in fields]
    row[4] = UUID(row[4])
    row.extend([datetime(2026, 9, 23, tzinfo=timezone.utc), UUID(link_id)])

    class FakeDb:
        def __init__(self):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self

        def execute(self, *args):
            pass

        def fetchone(self):
            self.reads += 1
            return None if self.reads == 1 else tuple(row)

    monkeypatch.setattr(links.db, "connect", FakeDb)
    result = links.link("mission", 42, 7, OBSERVATION_ID, RECORD_URL,
                        "relevant_to_topic", "скаут", reason, link_id)
    assert result["replayed"] is True
    assert result["link_id"] == link_id


def test_external_link_api_is_separate_from_expert_validation(monkeypatch):
    client = TestClient(app)
    called = {}

    def fake_link(*args):
        called["args"] = args
        return {"scientific_score_modified": False,
                "validation_status": "analyst_selected_not_expert_validated"}

    monkeypatch.setattr(links, "link", fake_link)
    response = client.post("/signals/mission/7/external-links?score_run_id=42", json={
        "observation_id": OBSERVATION_ID, "record_url": RECORD_URL,
        "assessment": "relevant_to_topic", "linked_by": "скаут",
        "rationale": "Выбрана конкретная запись после проверки названия.",
    })
    assert response.status_code == 201
    assert called["args"][:3] == ("mission", 42, 7)
    assert response.json()["validation_status"] == "analyst_selected_not_expert_validated"
    monkeypatch.setattr(links, "for_candidate", lambda *args: {"count": 0, "links": []})
    assert client.get("/signals/mission/7/external-links?score_run_id=42").json()["count"] == 0
