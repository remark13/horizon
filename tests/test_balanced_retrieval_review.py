from __future__ import annotations

import copy
import hashlib
import json

import pytest

from saia.balanced_retrieval_review import build_packet
from saia.retrieval_relevance_review import validate_submission


def job() -> dict:
    value = {
        "job_id": "00000000-0000-0000-0000-000000000101",
        "approved_query_plan_id": "00000000-0000-0000-0000-000000000102",
        "job_kind": "approved_balanced_discovery",
        "status": "succeeded",
        "result_role": "balanced_corpus_candidate_not_signals",
        "result_sha256": None,
        "payload": {"branches": [
            {"branch_id": "original-query", "query": "тканевая инженерия"},
            {"branch_id": "tissue", "query": "tissue engineering",
             "label_ru": "Тканевая инженерия"},
        ]},
        "result": {
            "result_role": "balanced_corpus_candidate_not_signals",
            "weak_signal_assessment_performed": False,
            "works": [{
                "canonical_key": "doi:10.1/example",
                "title": "A tissue engineering result",
                "abstract": "Primary experimental result.",
                "published_at": "2024-01-02",
                "urls": ["https://doi.org/10.1/example"],
                "source_ids": ["https://openalex.org/W1"],
                "branch_provenance": ["original-query", "tissue"],
                "source_provenance": ["openalex"],
            }],
        },
    }
    value["result_sha256"] = hashlib.sha256(json.dumps(
        value["result"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return value


def completed(template: dict) -> dict:
    value = copy.deepcopy(template)
    value["reviewer_id"] = "reviewer-a"
    value["independent_review_declared"] = True
    value["hidden_fields_not_seen_declared"] = True
    for row in value["annotations"]:
        row["answers"] = {"topical_relevance": "relevant", "evidence_role": "central"}
        row["rationale"] = "The experimental work directly addresses the target topic."
    return value


def test_packet_is_deterministic_complete_and_hides_merge_rank():
    first = build_packet(job())
    second = build_packet(job())
    assert first == second
    packet, template = first
    assert packet["selection_summary"]["assignments"] == 2
    assert packet["selection_summary"]["unique_documents"] == 1
    assert packet["selection_summary"]["all_returned_documents_included"] is True
    assert packet["gold_standard"] is False
    assert packet["calibration_allowed"] is False
    assert packet["production_change_allowed"] is False
    assert all(item["document"]["abstract_included"] for item in packet["items"])
    serialized = str(packet["items"])
    assert "branch_provenance" not in serialized
    assert "source_provenance" not in serialized
    assert "selection_rank" not in serialized
    assert len(template["annotations"]) == 2


def test_dynamic_packet_uses_existing_independent_submission_validation():
    packet, template = build_packet(job())
    validated = validate_submission(packet, completed(template))
    assert validated["complete"] is True
    assert validated["precision_available"] is False
    assert validated["production_change_allowed"] is False


def test_packet_refuses_wrong_or_unfinished_job_and_unknown_branch():
    value = job()
    value["status"] = "running"
    with pytest.raises(ValueError, match="дождитесь"):
        build_packet(value)
    value = job()
    value["result"]["works"][0]["branch_provenance"] = ["missing"]
    value["result_sha256"] = hashlib.sha256(json.dumps(
        value["result"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="неизвестную ветвь"):
        build_packet(value)
