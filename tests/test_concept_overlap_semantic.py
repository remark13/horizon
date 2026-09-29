import json

import pytest

from saia.concept_overlap_semantic import _pairwise, run
from saia.controlled_collection import sha256_file


class FakeEmbedder:
    def embed(self, texts):
        mapping = {"запрос": [1.0, 0.0], "Хорошая. текст": [1.0, 0.0],
                   "Плохая. текст": [0.0, 1.0]}
        return [mapping[text] for text in texts]


def test_pairwise_only_compares_same_case():
    rows = [{"case_id": "a", "label": "strict", "cosine": 0.9},
            {"case_id": "a", "label": "off_topic", "cosine": 0.2},
            {"case_id": "b", "label": "partial", "cosine": 0.1}]
    assert _pairwise(rows)["counts"] == {
        "strict_over_off_topic_correct": 1, "strict_over_off_topic_pairs": 1}


def test_semantic_diagnostic_checks_frozen_keys_and_ranks(tmp_path):
    proposals_path = tmp_path / "proposals.json"
    proposals_path.write_text(json.dumps({"version": "ru-concept-plan-diagnostic-v2",
                                           "rows": [{"case_id": "case", "query_ru": "запрос"}]}))
    packet_path = tmp_path / "packet.json"
    packet = {"version": "ru-concept-overlap-review-packet-v1",
              "proposals_sha256": sha256_file(proposals_path),
              "case_ids": ["case"], "rows": [
                  {"case_id": "case", "arxiv_id": "1", "title": "Хорошая",
                   "abstract": "текст", "matched_group_count": 2},
                  {"case_id": "case", "arxiv_id": "2", "title": "Плохая",
                   "abstract": "текст", "matched_group_count": 2}]}
    packet_path.write_text(json.dumps(packet))
    review_path = tmp_path / "review.json"
    review = {"version": "ru-concept-overlap-developer-review-v1",
              "policy": {"not_independent_expert_review": True}, "rows": [
                  {"case_id": "case", "arxiv_id": "1", "label": "strict"},
                  {"case_id": "case", "arxiv_id": "2", "label": "off_topic"}]}
    review_path.write_text(json.dumps(review))
    result = run(packet_path=packet_path, review_path=review_path,
                 proposals_path=proposals_path, embedder=FakeEmbedder(),
                 model_digest="frozen")
    assert result["embedded_texts"] == 3
    assert result["pairwise"]["counts"]["strict_over_off_topic_correct"] == 1
    assert result["sample_top_three_by_case"][0]["sample_top_three"][0]["arxiv_id"] == "1"
    review["rows"].pop()
    review_path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="Review labels"):
        run(packet_path=packet_path, review_path=review_path,
            proposals_path=proposals_path, embedder=FakeEmbedder(),
            model_digest="frozen")
