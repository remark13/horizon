import json
from hashlib import sha256

import pytest

from saia.cross_domain_relevance_packet import json_bytes
from saia.cross_domain_relevance_review import blank_template, compare


def _write(path, value):
    path.write_bytes(json_bytes(value))


def _review(packet_hash, reviewer_id, labels):
    return {"version": "cross-domain-relevance-review-v1",
            "packet_sha256": packet_hash,
            "reviewer_id": reviewer_id,
            "reviewer_kind": "independent_expert",
            "labels": labels}


def _label(identifier, relevance, role):
    return {"item_id": identifier, "topical_relevance": relevance,
            "evidence_role": role, "missing_qualifiers": [],
            "rationale": "Checked against the title and abstract."}


def _inputs(tmp_path):
    packet_path = tmp_path / "packet.json"
    audit_path = tmp_path / "audit.json"
    left_path = tmp_path / "left.json"
    right_path = tmp_path / "right.json"
    packet = {"version": "cross-domain-article-relevance-packet-v1",
              "items": [{"item_id": "item-a"}, {"item_id": "item-b"}]}
    _write(packet_path, packet)
    packet_hash = sha256(packet_path.read_bytes()).hexdigest()
    _write(audit_path, {"version": "cross-domain-article-relevance-audit-v1",
                        "packet_sha256": packet_hash})
    _write(left_path, _review(packet_hash, "expert-a", [
        _label("item-a", "yes", "primary_technical_result"),
        _label("item-b", "no", "background_only")]))
    _write(right_path, _review(packet_hash, "expert-b", [
        _label("item-a", "yes", "primary_technical_result"),
        _label("item-b", "partial", "background_only")]))
    return dict(packet_path=packet_path, audit_path=audit_path,
                left_review_path=left_path, right_review_path=right_path)


def test_review_comparison_requires_full_labels_and_lists_disagreements(tmp_path):
    paths = _inputs(tmp_path)
    report = compare(**paths)
    assert report["topical_relevance_agreement"]["exact_agreement"] == 0.5
    assert report["evidence_role_agreement"]["exact_agreement"] == 1
    assert report["disagreement_item_ids_for_adjudication"] == ["item-b"]
    assert report["policy"]["no_production_score_change"]


def test_blank_template_is_not_accepted_as_a_completed_review(tmp_path):
    paths = _inputs(tmp_path)
    template = blank_template(paths["packet_path"])
    assert len(template["labels"]) == 2
    assert all(row["topical_relevance"] is None for row in template["labels"])
    _write(paths["left_review_path"], template)
    with pytest.raises(ValueError, match="identity"):
        compare(**paths)


def test_review_comparison_rejects_missing_and_same_reviewer(tmp_path):
    paths = _inputs(tmp_path)
    right = json.loads(paths["right_review_path"].read_text())
    right["labels"].pop()
    _write(paths["right_review_path"], right)
    with pytest.raises(ValueError, match="every item"):
        compare(**paths)
    right = json.loads(paths["left_review_path"].read_text())
    _write(paths["right_review_path"], right)
    with pytest.raises(ValueError, match="distinct reviewer"):
        compare(**paths)
