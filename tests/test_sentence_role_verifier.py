import json
from pathlib import Path

import pytest

from saia.sentence_role_verifier import passages, prompt, validate_response
from scripts.benchmark_sentence_role_verifier import _evaluation_pairs


def test_passages_preserve_title_and_abstract_and_number_them():
    source = passages("A study", "First sentence. Second sentence.")
    assert [item["id"] for item in source] == [0, 1]
    assert source[0]["section"] == "title"
    assert source[1]["text"] == "First sentence. Second sentence."
    assert "[1] abstract" in prompt("нейроморфный процессор", source)


def test_direct_requires_existing_abstract_evidence():
    source = passages("A chip", "We fabricated a chip and measured its power use.")
    accepted = validate_response({"response": json.dumps({
        "decision": "direct", "evidence_ids": [1], "reason_ru": "Показан эксперимент."})}, source)
    assert accepted["evidence_passages"][0]["text"].startswith("We fabricated")
    for ids in ([0], [2], [1, 1], [True]):
        with pytest.raises(ValueError):
            validate_response({"response": json.dumps({
                "decision": "direct", "evidence_ids": ids,
                "reason_ru": "Показан эксперимент."})}, source)


def test_missing_abstract_cannot_become_direct_and_uncertain_can_abstain():
    source = passages("A chip", "")
    with pytest.raises(ValueError):
        validate_response({"response": json.dumps({
            "decision": "direct", "evidence_ids": [0],
            "reason_ru": "Заявлен результат."})}, source)
    assert validate_response({"response": json.dumps({
        "decision": "uncertain", "evidence_ids": [],
        "reason_ru": "Недостаточно текста."})}, source)["decision"] == "uncertain"


def test_second_development_set_is_frozen_to_twelve_distinct_packet_items():
    root = Path(__file__).resolve().parents[1]
    pairs = _evaluation_pairs(
        root / "evaluation/goal-cross-domain-relevance-v2.packet.json",
        root / "config/role-verifier-dev12b-developer-labels.v1.json")
    assert len(pairs) == 12
    assert len({item["item_id"] for item, _ in pairs}) == 12
