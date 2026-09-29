import json

import pytest

from saia.controlled_collection import sha256_file
from saia.priority_relevance_review import evaluate


def _fixture(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"version":"pilot"}\n', encoding="utf-8")
    packet = tmp_path / "packet.json"
    packet.write_text(json.dumps({
        "version": "priority-pilot-relevance-review-v1",
        "source_manifest_sha256": sha256_file(manifest),
        "selection": {"items": 1, "empty_cases": []},
        "items": [{"item_id": "rel-1", "target_topic": "Topic",
                   "case_role": "customer_supplied_example"}],
        "review_choices": {"topical_relevance": ["yes", "partial", "no", "uncertain"],
                           "evidence_role": ["primary_technical_result", "review"]},
    }), encoding="utf-8")
    review = tmp_path / "review.json"
    payload = {"version": "priority-arxiv-pilot-developer-review-v1",
               "packet_sha256": sha256_file(packet),
               "corpus_manifest_sha256": sha256_file(manifest),
               "reviewer_role": "developer_diagnostic_not_independent_expert",
               "labels": [["rel-1", "partial", "primary_technical_result",
                           "A related component only."]]}
    review.write_text(json.dumps(payload), encoding="utf-8")
    return packet, manifest, review, payload


def test_review_counts_and_non_independence(tmp_path):
    packet, manifest, review, _ = _fixture(tmp_path)
    result = evaluate(packet_path=packet, corpus_manifest_path=manifest, review_path=review)
    assert result["document_topic_relevance_counts"] == {"partial": 1}
    assert result["interpretation"]["not_top15_precision"] is True
    assert result["by_case_role"]["customer_supplied_example"] == {"partial": 1}


@pytest.mark.parametrize("change", [
    lambda payload: payload.update(packet_sha256="wrong"),
    lambda payload: payload["labels"].append(payload["labels"][0]),
    lambda payload: payload["labels"][0].__setitem__(1, "invented"),
    lambda payload: payload.update(reviewer_role="independent_expert"),
])
def test_review_rejects_invalid_claim_or_labels(tmp_path, change):
    packet, manifest, review, payload = _fixture(tmp_path)
    change(payload)
    review.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        evaluate(packet_path=packet, corpus_manifest_path=manifest, review_path=review)
