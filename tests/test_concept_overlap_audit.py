import json

import pytest

from saia.concept_overlap_audit import audit


def _files(tmp_path):
    packet = tmp_path / "packet.json"
    review = tmp_path / "review.json"
    packet.write_text(json.dumps({
        "version": "ru-concept-overlap-review-packet-v1",
        "case_ids": ["case"],
        "rows": [{"case_id": "case", "arxiv_id": "1234.56789"}]}))
    review.write_text(json.dumps({
        "version": "ru-concept-overlap-developer-review-v1",
        "policy": {"not_independent_expert_review": True},
        "rows": [{"case_id": "case", "arxiv_id": "1234.56789",
                  "label": "strict", "reason_ru": "Подходит"}]}))
    return packet, review


def test_review_audit_requires_complete_matching_packet(tmp_path):
    packet, review = _files(tmp_path)
    output = tmp_path / "audit.json"
    result = audit(packet_path=packet, review_path=review, output_path=output)
    assert result["rows"] == 1
    assert result["label_counts"]["strict"] == 1
    with pytest.raises(FileExistsError):
        audit(packet_path=packet, review_path=review, output_path=output)


def test_review_audit_rejects_missing_or_foreign_label(tmp_path):
    packet, review = _files(tmp_path)
    payload = json.loads(review.read_text())
    payload["rows"][0]["arxiv_id"] = "other"
    review.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="foreign"):
        audit(packet_path=packet, review_path=review,
              output_path=tmp_path / "audit.json")
