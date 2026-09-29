"""Validate source-grounded developer labels without opening the holdout."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


PACKET_VERSION = "cross-domain-composition-pairs-v2"
REVIEW_VERSION = "cross-domain-composition-developer-review-v1"
CHOICES = {"yes", "no", "uncertain"}
FIELDS = ("same_research_problem", "same_technical_mechanism",
          "one_narrow_technology_line")


def validate(packet_path: Path, review_path: Path) -> dict:
    packet_hash = sha256_file(packet_path)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (packet.get("version") != PACKET_VERSION or
            review.get("version") != REVIEW_VERSION or
            review.get("packet_sha256") != packet_hash):
        raise ValueError("Review does not bind the frozen packet")
    cases = {case["case_id"]: case for case in packet["cases"]}
    if len(cases) != len(packet["cases"]):
        raise ValueError("Duplicate packet case")
    labels = review["labels"]
    if len({row["case_id"] for row in labels}) != len(labels):
        raise ValueError("Duplicate review case")
    expected = {case_id for case_id, case in cases.items()
                if case["partition"] == "development" and
                case["sample_role"] == "deterministic_random"}
    if {row["case_id"] for row in labels} != expected:
        raise ValueError("Developer review must cover only the frozen development sample")
    for row in labels:
        case = cases[row["case_id"]]
        if any(row[field] not in CHOICES for field in FIELDS):
            raise ValueError("Invalid judgement")
        if (row["one_narrow_technology_line"] == "yes" and
                (row["same_research_problem"] != "yes" or
                 row["same_technical_mechanism"] != "yes")):
            raise ValueError("Positive line conflicts with its stated facets")
        if not row.get("reason") or len(row["reason"]) < 30:
            raise ValueError("Missing rationale")
        for side in ("a", "b"):
            quote = row[f"evidence_quote_{side}"]
            if not quote or quote not in case[f"paper_{side}"]["abstract"]:
                raise ValueError(f"Quote does not occur in abstract: {row['case_id']}/{side}")
    domains = sorted({case["domain"] for case in cases.values()})
    source_cards = {}
    for source in packet["sources"]:
        source_path = packet_path.resolve().parents[1] / source["path"]
        if sha256_file(source_path) != source["sha256"]:
            raise ValueError("Saved system baseline changed")
        source_packet = json.loads(source_path.read_text(encoding="utf-8"))
        for card in source_packet["cards"]:
            key = (source["domain"], card["composition_sha256"])
            if key in source_cards:
                raise ValueError("Duplicate system composition")
            source_cards[key] = card
    compared = []
    for row in labels:
        case = cases[row["case_id"]]
        card = source_cards[(case["domain"], case["card_composition_sha256"])]
        compared.append({"case_id": row["case_id"], "domain": case["domain"],
                         "one_narrow_technology_line": row["one_narrow_technology_line"],
                         "baseline_scope_warning": bool(card["screening"].get("scope_warning")),
                         "baseline_screening_state": card["screening"]["state"]})
    return {
        "packet_sha256": packet_hash,
        "review_sha256": sha256_file(review_path),
        "reviewed_cases": len(labels),
        "unreviewed_holdout_cases": sum(case["partition"] == "holdout"
                                        for case in cases.values()),
        "unreviewed_other_development_cases": sum(
            case["partition"] == "development" and
            case["sample_role"] != "deterministic_random"
            for case in cases.values()),
        "one_narrow_line": dict(Counter(row["one_narrow_technology_line"]
                                        for row in labels)),
        "by_domain": {domain: dict(Counter(
            row["one_narrow_technology_line"] for row in labels
            if cases[row["case_id"]]["domain"] == domain)) for domain in domains},
        "baseline_scope_warning_comparison": {
            "negative_pair_cases": sum(row["one_narrow_technology_line"] == "no"
                                       for row in compared),
            "negative_pair_cases_with_scope_warning": sum(
                row["one_narrow_technology_line"] == "no" and row["baseline_scope_warning"]
                for row in compared),
            "positive_pair_cases_with_scope_warning": sum(
                row["one_narrow_technology_line"] == "yes" and row["baseline_scope_warning"]
                for row in compared),
            "rows": compared,
            "interpretation_limit": "One sampled pair can refute a card's narrowness, but cannot establish complete card coherence or measured precision.",
        },
        "label_origin": review["reviewer_role"],
        "independent_quality_claim": False,
        "production_rule_changed": False,
    }
