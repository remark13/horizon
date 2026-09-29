"""Build traceable diagnostic line cards from frozen arXiv and developer review.

These cards are not product weak-signal verdicts or independent quality scores.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "line-evidence-pilot-v1"
DIRECT = "direct_primary_result"
ALLOWED = {DIRECT, "review_or_survey", "background_mention_or_adjacent",
           "off_topic", "unclear"}


def _unique(rows: list[dict], field: str) -> dict[str, dict]:
    result = {row[field]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"Duplicate {field}")
    return result


def build_cards(*, temporal: dict, packet: dict, selection: dict,
                review: dict, packet_sha256: str,
                input_sha256: dict[str, str]) -> dict:
    if (temporal.get("weak_signal_assessment_performed") is not False
            or temporal.get("accuracy_evaluated") is not False
            or temporal["source"]["source_role"] != "pinned_arxiv_metadata_snapshot_not_world_science"
            or temporal["period"]["window"] != "September-August complete years"):
        raise ValueError("Temporal series has unsupported evidentiary status")
    if (review.get("packet_sha256") != packet_sha256
            or review.get("independent_expert_review") is not False
            or review.get("reviewer_role") != "developer"
            or packet.get("plan_sha256") != selection.get("plan_sha256")
            or packet.get("plan_sha256") != temporal.get("config_sha256")):
        raise ValueError("Packet, source plan or review provenance differs")
    papers = _unique(packet["items"], "item_id")
    selected = _unique(selection["selection"], "item_id")
    judged = _unique(review["rows"], "item_id")
    if not papers or set(papers) != set(selected) or set(papers) != set(judged):
        raise ValueError("Review must cover the exact selected packet")
    lines = _unique(temporal["lines"], "line_id")
    if not lines or set(review["line_scope"]) != set(lines):
        raise ValueError("Line scopes differ from temporal report")
    by_line = defaultdict(list)
    for item_id, paper in papers.items():
        row, selection_row = judged[item_id], selected[item_id]
        if (paper["line_id"] not in lines
                or row["directness"] not in ALLOWED
                or not row.get("reason")
                or paper["line_id"] != selection_row["line_id"]
                or paper["arxiv_id"] != selection_row["arxiv_id"]):
            raise ValueError("Review item is incomplete or source-linked incorrectly")
        by_line[paper["line_id"]].append((paper, row, selection_row))
    cards = []
    for line_id, line in lines.items():
        annual = line["annual"]
        if (len(annual) != len(temporal["windows"])
                or sum(window["line_unique_ids"] for window in annual)
                != line["total_unique_ids"]):
            raise ValueError("Annual line counts do not reconcile")
        sampled = by_line[line_id]
        labels = Counter(row["directness"] for _, row, _ in sampled)
        direct = sorted(((paper, row) for paper, row, _ in sampled
                         if row["directness"] == DIRECT),
                        key=lambda pair: (pair[0]["first_submission_date"],
                                          pair[0]["arxiv_id"]))
        cards.append({
            "line_id": line_id,
            "title_ru": line["label_ru"],
            "status": "provisional_research_line_not_verified_weak_signal",
            "generated_from_broad_user_query": False,
            "reason_not_independent_discovery": "Narrow search concepts were fixed by the developer; the broad user query did not generate this line automatically.",
            "publication_source": "pinned local arXiv metadata only",
            "time_window": temporal["period"],
            "literal_match_total": line["total_unique_ids"],
            "annual_literal_matches": [
                {"from": window["from"], "to_exclusive": window["to_exclusive"],
                 "count": window["line_unique_ids"]} for window in annual],
            "outside_prior_broad_bas_union": line["outside_frozen_bas_union"],
            "updated_after_first_submission_window": line[
                "current_metadata_updated_after_first_submission_window"],
            "developer_title_abstract_sample": {
                "sample_size": len(sampled),
                "direct_primary_result": labels[DIRECT],
                "adjacent_or_background": labels["background_mention_or_adjacent"],
                "review_or_survey": labels["review_or_survey"],
                "off_topic": labels["off_topic"],
                "unclear": labels["unclear"],
                "selection_policy": selection["selection_policy"],
                "not_independent_precision_estimate": True,
            },
            "sample_direct_source_examples": [
                {"arxiv_id": paper["arxiv_id"], "url": paper["url"],
                 "title": paper["title"], "first_submission_date": paper[
                     "first_submission_date"],
                 "developer_mechanism": row["technical_mechanism"]}
                for paper, row in direct[:5]],
            "relative_growth_in_bas_area": "unknown",
            "first_mention_in_original_version": "unknown",
            "independent_expert_validation": "not_done",
            "limitations": [
                "Literal title/abstract matches have not all been reviewed for relevance or primary contribution.",
                "The previous broad BAS predicate misses most line papers, so it is not a valid area denominator.",
                "Current metadata may have changed after original submission; first mention is not established.",
                "The sample was judged by a developer from title and abstract, not independently or from full text.",
            ],
        })
    return {"version": VERSION, "input_sha256": input_sha256,
            "source_roles": {"arxiv": "scientific_metadata",
                             "review": "developer_diagnostic_not_gold"},
            "cards": cards,
            "accuracy_or_weak_signal_confirmation_claimed": False}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("temporal", "packet", "selection", "review", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    names = ("temporal", "packet", "selection", "review")
    paths = {name: getattr(args, name) for name in names}
    data = {name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in paths.items()}
    result = build_cards(**data, packet_sha256=sha256_file(args.packet),
                         input_sha256={name: sha256_file(path)
                                       for name, path in paths.items()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"cards": len(result["cards"]),
                      "direct_sample": sum(card["developer_title_abstract_sample"][
                          "direct_primary_result"] for card in result["cards"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
