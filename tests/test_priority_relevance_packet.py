from __future__ import annotations

import json
from pathlib import Path
import pytest

from saia.priority_relevance_packet import FOLLOWUP_VERSION, VERSION, build


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.local_data
def test_saved_review_packet_is_reproducible_and_unlabelled() -> None:
    corpus = ROOT / "data/processed/priority-arxiv-pilot-corpus-v1"
    catalog = ROOT / "data/reference/priority_catalog/v1/catalog.json"
    cases = ROOT / "config/priority-arxiv-pilot.v2.json"
    packet = json.loads((ROOT / "evaluation/priority-arxiv-pilot-relevance-v1.packet.json").read_text())
    assert build(corpus_dir=corpus, catalog_path=catalog, cases_path=cases) == packet
    assert packet["version"] == VERSION
    assert packet["selection"]["items"] == len(packet["items"]) == 67
    assert packet["selection"]["empty_cases"] == [
        "customer-signal-011", "national-area-029", "national-area-046"
    ]
    assert packet["limits"]["weak_signal_labels_present"] is False
    assert packet["limits"]["query_tuning_independent_holdout"] is False
    assert all(item["review"] == {
        "topical_relevance": None, "evidence_role": None, "rationale": None
    } for item in packet["items"])
    assert all(item["document"]["url"] ==
               f"https://arxiv.org/abs/{item['document']['arxiv_id']}"
               for item in packet["items"])


@pytest.mark.local_data
def test_followup_review_packet_is_reproducible_and_disjoint() -> None:
    corpus = ROOT / "data/processed/priority-arxiv-pilot-corpus-v1"
    catalog = ROOT / "data/reference/priority_catalog/v1/catalog.json"
    cases = ROOT / "config/priority-arxiv-pilot.v2.json"
    first = json.loads((ROOT / "evaluation/priority-arxiv-pilot-relevance-v1.packet.json").read_text())
    second = json.loads((ROOT / "evaluation/priority-arxiv-pilot-relevance-v2.packet.json").read_text())
    assert build(corpus_dir=corpus, catalog_path=catalog, cases_path=cases,
                 offset_per_case=6) == second
    assert second["version"] == FOLLOWUP_VERSION
    assert second["selection"]["items"] == len(second["items"]) == 41
    assert {item["item_id"] for item in first["items"]}.isdisjoint(
        {item["item_id"] for item in second["items"]})
    assert second["limits"]["offset_followup_is_not_independent_weak_signal_detection"]
