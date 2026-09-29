from __future__ import annotations

import pytest

from saia import balanced_retrieval_adjudication as adjudication


JOB_ID = "00000000-0000-0000-0000-000000000111"
LEFT_ID = "00000000-0000-0000-0000-000000000112"
RIGHT_ID = "00000000-0000-0000-0000-000000000113"


def packet():
    return {
        "package_id": "00000000-0000-0000-0000-000000000114",
        "packet_payload_sha256": "a" * 64,
        "items": [
            {"item_id": "one", "target_topic": "Topic A"},
            {"item_id": "two", "target_topic": "Topic A"},
            {"item_id": "three", "target_topic": "Topic B"},
        ],
    }


def saved(identifier):
    return {
        "submission_id": identifier,
        "reviewer_id": "Reviewer A" if identifier == LEFT_ID else "Reviewer B",
    }


def decisions():
    return [
        {"item_id": "one", "topical_relevance": "relevant",
         "rationale": "The publication directly addresses the target topic.", "sources": []},
        {"item_id": "two", "topical_relevance": "not_relevant",
         "rationale": "The phrase is incidental and the main topic is different.", "sources": []},
        {"item_id": "three", "topical_relevance": "relevant",
         "rationale": "The publication directly addresses the target topic.", "sources": []},
    ]


def configure(monkeypatch):
    monkeypatch.setattr(adjudication, "from_job_id", lambda value: (packet(), {}))
    monkeypatch.setattr(
        adjudication, "read_submission", lambda job_id, identifier: saved(identifier)
    )


def test_explicit_adjudication_yields_scoped_precision_only(monkeypatch):
    configure(monkeypatch)
    result = adjudication.build(
        JOB_ID, LEFT_ID, RIGHT_ID, "Adjudicator", decisions()
    )
    assert result["metrics"]["precision_on_returned_assignments"] == 2 / 3
    assert result["metrics"]["by_target_topic"] == [
        {"target_topic": "Topic A", "assignments": 2, "relevant": 1,
         "not_relevant": 1, "precision_on_returned_assignments": 0.5},
        {"target_topic": "Topic B", "assignments": 1, "relevant": 1,
         "not_relevant": 0, "precision_on_returned_assignments": 1.0},
    ]
    assert result["precision_available"] is True
    assert result["recall_available"] is False
    assert result["weak_signal_accuracy_measured"] is False
    assert result["production_change_allowed"] is False


def test_adjudicator_is_independent_and_must_make_binary_complete_decisions(monkeypatch):
    configure(monkeypatch)
    with pytest.raises(ValueError, match="отличаться"):
        adjudication.build(JOB_ID, LEFT_ID, RIGHT_ID, "reviewer a", decisions())
    incomplete = decisions()[:-1]
    with pytest.raises(ValueError, match="каждому"):
        adjudication.build(JOB_ID, LEFT_ID, RIGHT_ID, "Adjudicator", incomplete)
    uncertain = decisions()
    uncertain[0]["topical_relevance"] = "uncertain"
    with pytest.raises(ValueError, match="бинарной"):
        adjudication.build(JOB_ID, LEFT_ID, RIGHT_ID, "Adjudicator", uncertain)


def test_status_never_starts_pipeline_automatically(monkeypatch):
    monkeypatch.setattr(
        "saia.balanced_retrieval_review_store.history",
        lambda job_id, limit: {
            "package_id": "00000000-0000-0000-0000-000000000114",
            "total": 2,
            "submissions": [{"reviewer_id": "A"}, {"reviewer_id": "B"}],
        },
    )
    monkeypatch.setattr(adjudication, "history", lambda job_id, limit: {
        "adjudications": [{"adjudication_id": "one"}],
    })
    result = adjudication.status(JOB_ID)
    assert result["state"] == "adjudicated_retrieval_only"
    assert result["retrieval_validation_complete"] is True
    assert result["scientific_pipeline_started"] is False
    assert result["automatic_pipeline_start"] is False
    assert result["materialization_authorized"] is False
