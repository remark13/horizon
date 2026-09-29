"""Audit developer document-topic labels without calling them independent precision."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "priority-arxiv-pilot-developer-review-v1"
FOLLOWUP_VERSION = "priority-arxiv-pilot-developer-review-v2"
REPORT_VERSION = "priority-arxiv-pilot-developer-review-report-v1"


def evaluate(*, packet_path: Path, corpus_manifest_path: Path,
             review_path: Path) -> dict:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    packet_version = packet.get("version")
    if packet_version not in {"priority-pilot-relevance-review-v1",
                              "priority-pilot-relevance-review-v2"}:
        raise ValueError("Unexpected review packet version")
    expected_review_version = (VERSION if packet_version.endswith("v1")
                               else FOLLOWUP_VERSION)
    if review.get("version") != expected_review_version:
        raise ValueError("Unexpected developer review version")
    if review.get("packet_sha256") != sha256_file(packet_path):
        raise ValueError("Review is not bound to this packet")
    if review.get("corpus_manifest_sha256") != sha256_file(corpus_manifest_path):
        raise ValueError("Review is not bound to this corpus")
    if packet.get("source_manifest_sha256") != sha256_file(corpus_manifest_path):
        raise ValueError("Packet is not bound to this corpus")
    if review.get("reviewer_role") != "developer_diagnostic_not_independent_expert":
        raise ValueError("Reviewer role would overstate independence")
    items = packet["items"]
    by_id = {item["item_id"]: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("Duplicate packet item ID")
    labels = review["labels"]
    if len(labels) != len(items):
        raise ValueError("Review does not cover every packet item")
    seen = set()
    by_topic: dict[str, Counter[str]] = defaultdict(Counter)
    by_role: dict[str, Counter[str]] = defaultdict(Counter)
    evidence_roles = Counter()
    for label in labels:
        if not isinstance(label, list) or len(label) != 4:
            raise ValueError("Each label needs ID, relevance, evidence role and reason")
        item_id, relevance, evidence_role, reason = label
        if item_id not in by_id or item_id in seen:
            raise ValueError("Unknown or duplicate labelled item")
        seen.add(item_id)
        if relevance not in packet["review_choices"]["topical_relevance"]:
            raise ValueError("Unknown topical relevance choice")
        if evidence_role not in packet["review_choices"]["evidence_role"]:
            raise ValueError("Unknown evidence role choice")
        if not isinstance(reason, str) or len(reason.strip()) < 16:
            raise ValueError("Each label requires a substantive reason")
        item = by_id[item_id]
        by_topic[item["target_topic"]][relevance] += 1
        by_role[item["case_role"]][relevance] += 1
        evidence_roles[evidence_role] += 1
    if seen != set(by_id):
        raise ValueError("Review item coverage mismatch")
    total = Counter(label[1] for label in labels)
    return {
        "version": REPORT_VERSION,
        "packet_sha256": sha256_file(packet_path),
        "review_sha256": sha256_file(review_path),
        "corpus_manifest_sha256": sha256_file(corpus_manifest_path),
        "sample": packet["selection"],
        "document_topic_relevance_counts": dict(sorted(total.items())),
        "evidence_role_counts": dict(sorted(evidence_roles.items())),
        "by_case_role": {role: dict(sorted(counts.items()))
                         for role, counts in sorted(by_role.items())},
        "by_topic": {topic: dict(sorted(counts.items()))
                     for topic, counts in sorted(by_topic.items())},
        "interpretation": {
            "manual_developer_diagnostic": True,
            "not_independent_expert_labels": True,
            "not_top15_precision": True,
            "not_all_189_catalog_rows": True,
            "not_weak_signal_labels": True,
            "no_proof_of_ranking_improvement": True,
            "current_arxiv_abstracts_not_full_text": True,
            "sampled_retrieval_candidates_not_random_corpus_background": True,
        },
    }
