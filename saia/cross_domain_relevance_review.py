"""Validate complete article-level reviews; compare, never auto-approve them.

Reviewer independence is declared by the supplied files and cannot be proven
from identifiers. This module does not feed labels into the production score.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "cross-domain-relevance-review-v1"
COMPARISON_VERSION = "cross-domain-relevance-agreement-v1"
RELEVANCE = {"yes", "partial", "no", "uncertain"}
EVIDENCE_ROLES = {"primary_technical_result", "review", "application_or_case",
                  "background_only", "market_or_infrastructure", "uncertain"}


def blank_template(packet_path: Path) -> dict:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if packet.get("version") != "cross-domain-article-relevance-packet-v1":
        raise ValueError("Unknown article-level review packet")
    identifiers = [item["item_id"] for item in packet["items"]]
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("Packet item identities are missing or repeated")
    return {"version": VERSION, "packet_sha256": sha256_file(packet_path),
            "reviewer_id": None, "reviewer_kind": None,
            "labels": [{"item_id": identifier, "topical_relevance": None,
                        "evidence_role": None, "missing_qualifiers": [],
                        "rationale": None} for identifier in identifiers],
            "instructions": "Copy separately for each reviewer. Fill every label from the "
                            "source title/abstract/link; do not see another review. "
                            "Use uncertain if evidence is insufficient. "
                            "Do not label future success or weak-signal strength."}


def _validate_one(packet: dict, packet_sha: str, review_path: Path) -> dict:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (review.get("version") != VERSION
            or review.get("packet_sha256") != packet_sha
            or not isinstance(review.get("reviewer_id"), str)
            or not review["reviewer_id"].strip()
            or review.get("reviewer_kind") not in {"independent_expert", "developer"}):
        raise ValueError("Review identity, role or packet hash invalid")
    rows = review.get("labels")
    expected = {item["item_id"] for item in packet["items"]}
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or {row.get("item_id") for row in rows if isinstance(row, dict)} != expected):
        raise ValueError("Review must label every item exactly once")
    by_id = {}
    for row in rows:
        if (not isinstance(row, dict)
                or row.get("topical_relevance") not in RELEVANCE
                or row.get("evidence_role") not in EVIDENCE_ROLES
                or not isinstance(row.get("rationale"), str)
                or not 8 <= len(row["rationale"].strip()) <= 1000
                or not isinstance(row.get("missing_qualifiers"), list)
                or len(row["missing_qualifiers"]) > 6
                or any(not isinstance(value, str) or not value.strip()
                       or len(value) > 160 for value in row["missing_qualifiers"])):
            raise ValueError("Incomplete or invalid article-level review label")
        by_id[row["item_id"]] = row
    return {"reviewer_id": review["reviewer_id"],
            "reviewer_kind": review["reviewer_kind"],
            "sha256": sha256_file(review_path), "labels": by_id}


def _agreement(left: list[str], right: list[str], categories: set[str]) -> dict:
    if len(left) != len(right) or not left:
        raise ValueError("Agreement requires complete paired labels")
    n = len(left)
    observed = sum(a == b for a, b in zip(left, right)) / n
    a_counts, b_counts = Counter(left), Counter(right)
    expected = sum((a_counts[value] / n) * (b_counts[value] / n)
                   for value in categories)
    kappa = ((observed - expected) / (1 - expected)
             if expected < 1 else None)
    return {"items": n, "exact_agreement": observed,
            "chance_expected_agreement": expected,
            "cohen_kappa": kappa,
            "kappa_undefined_for_single_category": expected == 1}


def compare(*, packet_path: Path, audit_path: Path,
            left_review_path: Path, right_review_path: Path) -> dict:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    packet_sha = sha256_file(packet_path)
    if (packet.get("version") != "cross-domain-article-relevance-packet-v1"
            or audit.get("version") != "cross-domain-article-relevance-audit-v1"
            or audit.get("packet_sha256") != packet_sha):
        raise ValueError("Packet and audit are not the same frozen sample")
    if (not packet.get("items")
            or len({item["item_id"] for item in packet["items"]})
            != len(packet["items"])):
        raise ValueError("Packet item identities are missing or repeated")
    left = _validate_one(packet, packet_sha, left_review_path)
    right = _validate_one(packet, packet_sha, right_review_path)
    if left["reviewer_id"] == right["reviewer_id"]:
        raise ValueError("Two distinct reviewer identifiers are required")
    ids = [item["item_id"] for item in packet["items"]]
    disagreement_ids = [identifier for identifier in ids
                        if (left["labels"][identifier]["topical_relevance"]
                            != right["labels"][identifier]["topical_relevance"]
                            or left["labels"][identifier]["evidence_role"]
                            != right["labels"][identifier]["evidence_role"])]
    return {"version": COMPARISON_VERSION,
            "packet_sha256": packet_sha,
            "audit_sha256": sha256_file(audit_path),
            "reviewers": [{key: review[key] for key in
                           ("reviewer_id", "reviewer_kind", "sha256")}
                          for review in (left, right)],
            "topical_relevance_agreement": _agreement(
                [left["labels"][identifier]["topical_relevance"] for identifier in ids],
                [right["labels"][identifier]["topical_relevance"] for identifier in ids],
                RELEVANCE),
            "evidence_role_agreement": _agreement(
                [left["labels"][identifier]["evidence_role"] for identifier in ids],
                [right["labels"][identifier]["evidence_role"] for identifier in ids],
                EVIDENCE_ROLES),
            "disagreement_item_ids_for_adjudication": disagreement_ids,
            "policy": {"reviewer_independence_self_declared_not_proven": True,
                       "developer_review_not_independent_expert_label": True,
                       "no_production_score_change": True,
                       "not_precision_at_15_or_weak_signal_accuracy": True}}
