import copy
import csv
import io
import json

import pytest

from scripts.build_top15_full_composition_review import build, _sources


def fixture():
    config = {
        "version": "top15-full-composition-review-v1", "seed": "review-seed",
        "runs": [{"direction": "Область", "mission_id": "mission-1",
                  "score_run_id": 7, "expected_cards": 1}],
    }
    card = {
        "candidate_id": "candidate-secret", "composition_sha256": "composition-secret",
        "label": "Новая технология", "status": "weak_signal",
        "emergence_score": 0.9, "gates": [{"gate": "secret"}],
        "metrics": {"observed": {"doc_count": 2}, "publication_series": {"points": [
            {"start": "2026-01-01", "end": "2026-04-01", "topic_works": 2,
             "corpus_works": 10, "share": 0.2, "complete": True,
             "coverage_comparable": False}]}}}
    queue = {"mission_id": "mission-1", "score_run_id": 7,
             "provenance": [{"as_of_date": "2026-09-01"}],
             "queue": [{"rank": 1, "topic_id": 9, "card": card}]}
    works = {9: [
        {"title": "Paper A", "published_at": "2026-01-01",
         "sources": ["https://arxiv.org/abs/2601.00001"]},
        {"title": "Paper B", "published_at": "2026-02-01",
         "sources": ["https://doi.org/10.1/b"]},
    ]}
    return config, [queue], works


def test_full_packet_is_deterministic_and_rank_blinded():
    config, queues, works = fixture()
    packet, private, template = build(config, queues, works)
    assert (packet, private, template) == build(config, queues, works)
    assert packet["items"][0]["works_shown"] == 2
    assert packet["items"][0]["works_truncated"] is False
    assert len(packet["items"][0]["works"]) == 2
    assert "candidate-secret" not in json.dumps(packet)
    assert "composition-secret" not in json.dumps(packet)
    assert "weak_signal" not in json.dumps(packet["items"])
    assert "emergence_score" not in json.dumps(packet)
    assert "rank" not in packet["items"][0]
    assert private["mapping"][0]["rank"] == 1
    assert private["mapping"][0]["candidate_id"] == "candidate-secret"
    rows = list(csv.DictReader(io.StringIO(template)))
    assert len(rows) == 1 and rows[0]["item_id"] == packet["items"][0]["item_id"]


def test_v2_adds_direction_relevance_without_changing_v1_protocol():
    config, queues, works = fixture()
    v1_packet, _, v1_template = build(config, queues, works)
    config["version"] = "top15-full-composition-review-v2"
    v2_packet, v2_private, v2_template = build(config, queues, works)
    assert "direction_relevance" not in v1_packet["questions"]
    assert "direction_relevance" not in v1_template
    assert list(v2_packet["questions"])[0] == "direction_relevance"
    assert "direction_relevance" in v2_template
    assert v2_packet["version"].endswith("v2")
    assert v2_private["packet_payload_sha256"] == v2_packet["packet_payload_sha256"]
    assert v2_packet["items"] == v1_packet["items"]


def test_full_packet_refuses_missing_work_or_frozen_run_mismatch():
    config, queues, works = fixture()
    with pytest.raises(ValueError, match="works != observed"):
        build(config, queues, {9: works[9][:1]})
    changed = copy.deepcopy(queues)
    changed[0]["score_run_id"] = 8
    with pytest.raises(ValueError, match="frozen review"):
        build(config, changed, works)


def test_source_links_are_unique_and_keep_identifier_types():
    assert _sources([("openalex", "W123"), ("doi", "10.1/test"),
                     ("arxiv", "2601.00001"), ("openalex", "W123")]) == [
        "https://arxiv.org/abs/2601.00001",
        "https://doi.org/10.1/test",
        "https://openalex.org/W123",
    ]
