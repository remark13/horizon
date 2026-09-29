"""Validate a narrow, title-only developer audit against frozen card contents."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


DECISIONS = {"visible_mixture", "not_rejected_by_titles"}


def _source_link(work: dict) -> str | None:
    for item in work.get("identifiers") or []:
        kind, value = item.get("kind"), item.get("value")
        if not isinstance(value, str) or not value:
            continue
        if kind == "arxiv":
            return "https://arxiv.org/abs/" + value
        if kind == "doi":
            return "https://doi.org/" + value.removeprefix("https://doi.org/")
        if kind == "openalex":
            return "https://openalex.org/" + value.removeprefix("https://openalex.org/")
    return None


def assess(review: dict, source: dict) -> dict:
    if review.get("version") != "bas-top15-title-contrast-review-v1":
        raise ValueError("Unknown BAS title-review version")
    decisions = review.get("decisions") or []
    if not decisions or [row.get("rank") for row in decisions] != list(
            range(1, len(decisions) + 1)):
        raise ValueError("Review must cover consecutive displayed ranks")
    cards = source.get("cards") or []
    if len(cards) < len(decisions):
        raise ValueError("Saved card result has fewer rows than the review")
    rows = []
    for decision, card in zip(decisions, cards):
        if (card.get("rank") != decision["rank"]
                or card.get("candidate_id") != decision["candidate_id"]
                or decision.get("decision") not in DECISIONS):
            raise ValueError("Review is not aligned to the frozen card order")
        works = {work["work_id"]: work for work in card["works"]}
        pair = decision.get("contrast_work_ids")
        if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or any(identifier not in works for identifier in pair)):
            raise ValueError("Review cites a work outside its card")
        evidence = [{"work_id": identifier, "title": works[identifier]["title"],
                     "source_url": _source_link(works[identifier])}
                    for identifier in pair]
        rows.append({
            "rank": decision["rank"], "candidate_id": decision["candidate_id"],
            "display_label": card["label"], "member_count": card["member_count"],
            "screening_state": card["screening"]["state"],
            "system_scope_warning": bool(card["screening"].get("scope_warning")),
            "developer_title_diagnostic": decision["decision"],
            "reason": decision["reason"], "contrasting_works": evidence,
        })
    counts = Counter(row["developer_title_diagnostic"] for row in rows)
    screen = Counter(row["screening_state"] for row in rows)
    return {
        "version": review["version"],
        "source_type": "frozen_saved_full_arxiv_bas_candidate_cards",
        "annotation_role": review["annotation_role"],
        "question": review["question"],
        "reviewed_card_count": len(rows),
        "counts": dict(sorted(counts.items())),
        "screening_state_counts": dict(sorted(screen.items())),
        "visible_mixtures_with_existing_scope_warning": sum(
            row["developer_title_diagnostic"] == "visible_mixture"
            and row["system_scope_warning"] for row in rows),
        "scope_warning_is_a_label_only_diagnostic_not_a_coherence_test": True,
        "rows": rows,
        "limitations": review["limitations"],
    }


def run(config_path: Path, project_root: Path) -> dict:
    config_bytes = config_path.read_bytes()
    review = json.loads(config_bytes)
    source_path = project_root / review["source"]
    source_bytes = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != review["source_sha256"]:
        raise ValueError("Frozen BAS source differs from the review manifest")
    result = assess(review, json.loads(source_bytes))
    result["review_config_sha256"] = hashlib.sha256(config_bytes).hexdigest()
    result["source_sha256"] = source_hash
    result["source_path"] = review["source"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path,
                        default=Path("config/bas-top15-title-contrast-review-v1.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.config, Path(__file__).resolve().parents[1])
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            raise FileExistsError(args.output)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
