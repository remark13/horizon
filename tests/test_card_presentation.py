from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from saia import card_presentation as presentation
from saia.api import app


def packet():
    return presentation.input_packet({"candidate_id": 1, "composition_sha256": "a" * 64,
        "label": "Autonomous flight", "evidence": [{"title": "A research paper", "published_at": "2025-01-01",
        "rationale": "Публикация внутри тематической линии. Основание: A controller is proposed. [Фрагмент аннотации/названия; техническое преимущество требует проверки.]",
        "sources": [{"type": "arxiv", "url": "https://arxiv.org/abs/2501.00001"}]}]})


def content():
    return {"title_ru": "Автономный полёт", "description": {"text": "В работе предложен контроллер.", "evidence_ids": [1]},
            "problem": None, "advantage": None, "case": {"text": "Исследование нового контроллера.", "evidence_ids": [1]}}


def test_presentation_uses_exact_frozen_snippets_not_claimed_truth():
    value = packet()
    assert value["evidence"][0]["snippet"] == "A controller is proposed."
    assert value["only_sample_of_topic"] is True
    assert presentation.validate_content(content(), value)["advantage"] is None
    assert value["composition_sha256"] == "a" * 64


@pytest.mark.parametrize("mutation", ["foreign_id", "bool_id", "empty_sources", "english", "extra_score", "no_description"])
def test_invalid_presentation_is_not_accepted(mutation):
    value = content()
    if mutation == "foreign_id":
        value["description"]["evidence_ids"] = [2]
    elif mutation == "bool_id":
        value["description"]["evidence_ids"] = [True]
    elif mutation == "empty_sources":
        value["description"]["evidence_ids"] = []
    elif mutation == "english":
        value["title_ru"] = "Autonomous flight"
    elif mutation == "extra_score":
        value["confidence"] = 90
    else:
        value["description"] = None
    with pytest.raises(ValueError):
        presentation.validate_content(value, packet())


def test_presentation_rejects_user_supplied_cloud_model_url(monkeypatch):
    monkeypatch.setenv("SAIA_OLLAMA_URL", "https://external-model.example")
    assert presentation.models()["status"] == "unavailable"
    with pytest.raises(ValueError):
        presentation._base_url()


def test_input_packet_requires_real_evidence_and_bounds_text():
    with pytest.raises(ValueError):
        presentation.input_packet({"evidence": []})
    card = {"candidate_id": 1, "label": "x", "composition_sha256": "a" * 64,
            "evidence": [{"title": "a" * 2000, "rationale": "b" * 5000}] * 7}
    value = presentation.input_packet(card)
    assert len(value["evidence"]) == 5
    assert max(len(row["snippet"]) for row in value["evidence"]) == 1000


def test_presentation_title_is_short_and_prompt_change_invalidates_cache():
    value = content()
    value["title_ru"] = "Автономный полёт в сложных условиях " * 4
    with pytest.raises(ValueError):
        presentation.validate_content(value, packet())
    assert packet()["version"] == "russian-card-presentation-v2"


def test_presentation_api_is_explicit_model_separate_from_scientific_score(monkeypatch):
    calls = []
    monkeypatch.setattr(presentation, "generate", lambda *args: calls.append(args) or {"presentation": None, "scientific_results_modified": False})
    client = TestClient(app)
    response = client.post("/signals/m/1/presentation?score_run_id=12", json={"model": "qwen3:4b-instruct"})
    assert response.status_code == 200
    assert calls == [("m", 12, 1, "qwen3:4b-instruct")]
    assert response.json()["scientific_results_modified"] is False
    assert client.post("/signals/m/1/presentation?score_run_id=12", json={}).status_code == 422
