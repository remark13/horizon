"""Create an evidence-separated candidate card from a frozen phrase and review."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "auto-phrase-candidate-card-v1"
ROLES = {"model_or_system", "benchmark_or_dataset", "review_or_survey", "unclear"}


def _first_author_label(work: dict) -> str | None:
    authors = work.get("authors_parsed") or []
    if authors and isinstance(authors[0], list) and len(authors[0]) >= 2:
        return " ".join(str(part).casefold().strip() for part in authors[0][:2])
    return None


def build_card(*, proposals: dict, packet: dict, review: dict,
               packet_sha256: str, input_sha256: dict[str, str]) -> dict:
    if (packet["proposals_sha256"] != input_sha256["proposals"]
            or review["packet_sha256"] != packet_sha256
            or review.get("independent_expert_review") is not False
            or review.get("reviewer_role") != "developer"
            or packet.get("independent_discovery_benchmark") is not False
            or packet["phrase_en"] not in {row["phrase_en"] for row in proposals["proposals"]}):
        raise ValueError("Frozen phrase, packet or developer review does not match")
    works = {row["arxiv_id"]: row for row in packet["works"]}
    reviewed = {row["arxiv_id"]: row for row in review["rows"]}
    if (len(works) != len(packet["works"])
            or len(reviewed) != len(review["rows"])
            or not works or set(works) != set(reviewed)):
        raise ValueError("Complete one-to-one article review required")
    proposal = next(row for row in proposals["proposals"]
                    if row["phrase_en"] == packet["phrase_en"])
    if (proposal["title_work_count"] != len(works)
            or proposal["annual_title_counts"] != packet["annual_title_counts"]
            or sum(packet["annual_title_counts"]) != len(works)):
        raise ValueError("Phrase counts do not reconcile to work inventory")
    tasks: dict[str, list[dict]] = defaultdict(list)
    role_counts: Counter[str] = Counter()
    relevance_counts: Counter[str] = Counter()
    for identifier, work in works.items():
        label = reviewed[identifier]
        if (label.get("role") not in ROLES
                or label.get("bas_relevance") not in {"yes", "partial", "no", "unclear"}
                or not label.get("task") or not label.get("reason")
                or not work["url"].endswith("/" + identifier)):
            raise ValueError("Invalid role, relevance or source URL")
        role_counts[label["role"]] += 1
        relevance_counts[label["bas_relevance"]] += 1
        tasks[label["task"]].append({
            "arxiv_id": identifier, "url": work["url"],
            "title": work["title"],
            "first_submission_date": work["first_submission_date"],
            "developer_role": label["role"],
            "developer_mechanism": label["technical_mechanism"],
            "first_author_label": _first_author_label(work),
        })
    task_groups = []
    for task, papers in tasks.items():
        papers.sort(key=lambda row: (row["first_submission_date"], row["arxiv_id"]))
        task_groups.append({
            "task": task,
            "papers": papers,
            "role_counts": dict(Counter(row["developer_role"] for row in papers)),
        })
    task_groups.sort(key=lambda group: (-group["role_counts"].get("model_or_system", 0),
                                        group["task"]))
    strongest = task_groups[0]
    primary = [row for row in strongest["papers"]
               if row["developer_role"] == "model_or_system"]
    distinct_first_author_labels = len({row["first_author_label"] for row in primary
                                        if row["first_author_label"]})
    return {
        "version": VERSION,
        "input_sha256": input_sha256,
        "source_phrase": packet["phrase_en"],
        "source_phrase_rank": packet["proposal_rank"],
        "discovery_route": "broad BAS lexical parent -> unsupervised title phrase -> developer task split",
        "source_phrase_not_seeded_in_parent_plan": True,
        "posthoc_phrase_selection": True,
        "independent_discovery_benchmark": False,
        "phrase_family": {
            "arxiv_title_matches": len(works),
            "annual_title_matches": packet["annual_title_counts"],
            "parent_annual_counts": proposals["parent_annual_counts"],
            "developer_roles": dict(role_counts),
            "developer_bas_relevance": dict(relevance_counts),
            "one_narrow_mechanism_proven": False,
        },
        "task_groups": task_groups,
        "leading_narrow_task": {
            "task": strongest["task"],
            "developer_primary_system_count": len(primary),
            "first_matching_title_date": primary[0]["first_submission_date"] if primary else None,
            "distinct_first_author_strings_not_institutions": distinct_first_author_labels,
            "sources": primary,
            "title_phrase_series_complete_for_task": False,
            "status": "candidate_for_followup_not_verified_weak_signal",
        },
        "other_source_blocks": {
            "patents": "not_collected_for_this_candidate",
            "commercial_publications": "not_collected_for_this_candidate",
            "investment_deals": "not_collected_for_this_candidate",
        },
        "first_technology_publication_proven": False,
        "independent_group_diffusion_proven": False,
        "relative_growth_of_narrow_task_proven": False,
        "independent_expert_validation": "not_done",
        "confidence_percent": None,
        "limitations": [
            "The phrase was selected after viewing the machine-ranked list; this is a development example, not an unbiased recall test.",
            "The developer split uses current titles and abstracts, not full text or independent expert labels.",
            "The narrow task needs its own complete retrieval predicate and historical predecessor search.",
            "Different first-author strings do not prove independent research organizations.",
            "Patent and commercial blocks are unknown, not zero.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("proposals", "packet", "review", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    names = ("proposals", "packet", "review")
    paths = {name: getattr(args, name) for name in names}
    data = {name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in paths.items()}
    result = build_card(**data, packet_sha256=sha256_file(args.packet),
                        input_sha256={name: sha256_file(path)
                                      for name, path in paths.items()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"phrase_matches": result["phrase_family"]["arxiv_title_matches"],
                      "task": result["leading_narrow_task"]["task"],
                      "primary_systems": result["leading_narrow_task"][
                          "developer_primary_system_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
