"""Resolve frozen BAS group-level contrast claims to exact source sentences."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from saia.controlled_collection import sha256_file
from scripts.probe_grounded_task_facets_v3 import source_spans


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-top15-abstract-contrast-review-v1"
CONTRASTS = {"different_task", "off_domain_object", "parent_family_only"}


def _bound_json(relative: str, expected_hash: str) -> dict:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != expected_hash:
        raise ValueError("Frozen source or title review differs")
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(config: dict, source: dict, title_review: dict) -> dict:
    if (config.get("version") != VERSION
            or config.get("annotation_role") !=
            "single_developer_abstract_contrast_not_independent_gold"):
        raise ValueError("Unknown abstract-contrast protocol")
    decisions = config["decisions"]
    if len(decisions) != 15 or [row["rank"] for row in decisions] != list(range(1, 16)):
        raise ValueError("Expected one decision for every top-15 rank")
    cards = {card["rank"]: card for card in source["cards"]}
    earlier = {row["rank"]: row for row in title_review["decisions"]}
    rows = []
    for decision in decisions:
        rank = decision["rank"]
        card = cards[rank]
        prior = earlier[rank]
        ids = decision["work_ids"]
        if (card["candidate_id"] != decision["candidate_id"]
                or prior["candidate_id"] != decision["candidate_id"]
                or prior["contrast_work_ids"] != ids
                or len(ids) != 2 or len(set(ids)) != 2
                or decision["contrast_type"] not in CONTRASTS
                or not decision["reason"].strip()):
            raise ValueError(f"Invalid binding or contrast at rank {rank}")
        by_id = {work["work_id"]: work for work in card["works"]}
        items = []
        for identifier, evidence_ids in zip(ids, decision["evidence_ids"], strict=True):
            work = by_id.get(identifier)
            if not work or not 1 <= len(evidence_ids) <= 3:
                raise ValueError(f"Missing work or evidence at rank {rank}")
            spans = source_spans(work)
            if len(set(evidence_ids)) != len(evidence_ids) or any(
                    evidence_id == "T" or evidence_id not in spans
                    for evidence_id in evidence_ids):
                raise ValueError(f"Invalid abstract sentence at rank {rank}")
            arxiv_id = next((item["value"] for item in work.get("identifiers", [])
                             if item["kind"] == "arxiv"), None)
            if not arxiv_id:
                raise ValueError("Missing arXiv source ID")
            items.append({"work_id": identifier, "title": work["title"],
                          "url": f"https://arxiv.org/abs/{arxiv_id}",
                          "evidence": [{"id": evidence_id,
                                        "exact_abstract_sentence": spans[evidence_id]}
                                       for evidence_id in evidence_ids]})
        rows.append({"rank": rank, "candidate_id": card["candidate_id"],
                     "card_label": card["label"],
                     "card_work_count": card["member_count"],
                     "prior_title_decision": prior["decision"],
                     "contrast_type": decision["contrast_type"],
                     "reason": decision["reason"], "works": items})
    return {"version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": "single_developer_abstract_contrast_not_independent_gold",
            "source_sha256": config["source_sha256"],
            "title_review_sha256": config["title_review_sha256"],
            "counts": {"cards": len(rows),
                       "by_contrast_type": dict(Counter(
                           row["contrast_type"] for row in rows))},
            "rows": rows, "limitations": config["limitations"],
            "all_member_roles_reviewed": False,
            "independent_precision_at_15_measured": False,
            "production_changed": False}


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source = _bound_json(config["source"], config["source_sha256"])
    title_review = _bound_json(config["title_review"], config["title_review_sha256"])
    result = build_report(config, source, title_review)
    result["config_sha256"] = sha256_file(config_path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Abstract contrast report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
