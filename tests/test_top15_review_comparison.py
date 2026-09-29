import csv
import json

import pytest

from saia.top15_review_comparison import _digest, compare


QUESTIONS = ["direction_relevance", "research_line_coherence",
             "primary_result_evidence", "independent_groups_evidence",
             "weak_signal_at_cutoff"]


def saved_inputs(tmp_path):
    packet = {"version": "top15-full-composition-review-packet-v2",
              "questions": {question: question for question in QUESTIONS},
              "items": [{"item_id": "a", "direction": "БАС"},
                        {"item_id": "b", "direction": "БАС"}]}
    packet["packet_payload_sha256"] = _digest(packet)
    private = {"version": "top15-full-composition-review-private-key-v2",
               "packet_payload_sha256": packet["packet_payload_sha256"],
               "mapping": [{"item_id": item_id, "rank": rank,
                            "mission_id": "bas", "score_run_id": 1}
                           for rank, item_id in enumerate(("a", "b"), 1)]}
    private["private_payload_sha256"] = _digest(private)
    packet_path, private_path = tmp_path / "packet.json", tmp_path / "private.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    private_path.write_text(json.dumps(private), encoding="utf-8")
    return packet_path, private_path


def review(tmp_path, name, *, second_relevance="yes", complete=True):
    path = tmp_path / (name + ".csv")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["item_id", *QUESTIONS,
                                                    "rationale", "source_urls", "reviewer_id"])
        writer.writeheader()
        for item_id in (("a", "b") if complete else ("a",)):
            row = {question: "yes" for question in QUESTIONS}
            if item_id == "b":
                row["direction_relevance"] = second_relevance
            writer.writerow({"item_id": item_id, **row,
                             "rationale": "Обосновано первоисточником" if second_relevance != "yes" else "",
                             "source_urls": "", "reviewer_id": name})
    return path


def test_two_complete_reviews_report_disagreement_without_consensus(tmp_path):
    packet, private = saved_inputs(tmp_path)
    left = review(tmp_path, "expert-one")
    right = review(tmp_path, "expert-two", second_relevance="no")
    result = compare(packet, private, left, right)
    assert result["items"] == 2
    assert result["agreement_by_question"]["direction_relevance"]["exact_agreement"] == 0.5
    assert result["disagreement_item_ids_for_adjudication"] == ["b"]
    assert result["by_direction"][0]["reviewer_counts_not_consensus"]["expert-two"]["direction_relevance_yes"] == 1
    assert result["by_direction"][0]["full_top15_available"] is False
    assert result["precision_at_15"] is None


def test_incomplete_or_same_reviewer_never_opens_comparison(tmp_path):
    packet, private = saved_inputs(tmp_path)
    complete = review(tmp_path, "expert-one")
    incomplete = review(tmp_path, "expert-two", complete=False)
    with pytest.raises(ValueError, match="every item"):
        compare(packet, private, complete, incomplete)
    with pytest.raises(ValueError, match="distinct reviewer"):
        compare(packet, private, complete, complete)


def test_tampered_private_key_is_rejected(tmp_path):
    packet, private = saved_inputs(tmp_path)
    private_data = json.loads(private.read_text())
    private_data["mapping"][0]["rank"] = 15
    private.write_text(json.dumps(private_data), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        compare(packet, private, review(tmp_path, "expert-one"),
                review(tmp_path, "expert-two"))
