"""Freeze a small external published-signal reference before retrieval tuning.

The JRC list is a discoverability reference, not outcome truth. One societal
card is explicitly an out-of-scientific-scope control.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


SEED = "saia-external-holdout-2026-09-25-v1"
SCIENCE_CATEGORIES = (
    "Medicine and Biotechnology", "Materials", "Information & Communication technologies",
    "Energy", "Engineering & Physics", "Agriculture & Environment",
)


def freeze(public_catalog: Path, priority_catalog: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("Independent reference holdout is immutable")
    source = json.loads(public_catalog.read_text(encoding="utf-8"))
    priority = json.loads(priority_catalog.read_text(encoding="utf-8"))
    if source.get("version") != "public-signals-catalog-v5":
        raise ValueError("Unknown public-signal catalog")
    known = {item["title_original"].casefold().strip()
             for item in priority["customer_examples"]}
    known.update(item["title_ru"].casefold().strip()
                 for item in priority["national_search_areas"])
    groups = defaultdict(list)
    for row in source["records"]:
        if (row["source_id"] != "jrc_weak_signals_2021"
                or row["title"].casefold().strip() in known):
            continue
        groups[row["category"]].append(row)
    selection = []
    for category in (*SCIENCE_CATEGORIES, "Societal issues"):
        wanted = 1 if category == "Societal issues" else 2
        ordered = sorted(groups[category], key=lambda row: (
            hashlib.sha256((SEED + ":" + row["id"]).encode()).hexdigest(), row["id"]))
        if len(ordered) < wanted:
            raise ValueError("Not enough external references in a category")
        for row in ordered[:wanted]:
            selection.append({"id": row["id"], "title": row["title"],
                              "category": category, "source_url": row["source_url"],
                              "role": ("out_of_scientific_scope_control" if category == "Societal issues"
                                       else "external_published_signal_discoverability_reference")})
    report = {"version": "priority-external-reference-holdout-v1",
              "selection_seed": SEED,
              "input_sha256": {"public_catalog": sha256_file(public_catalog),
                               "priority_catalog": sha256_file(priority_catalog)},
              "counts": {"total": len(selection),
                         "by_category": dict(sorted(Counter(row["category"]
                                                        for row in selection).items()))},
              "items": selection,
              "policy": {"not_used_to_form_priority_query_phrases": True,
                         "not_a_ground_truth_of_future_success": True,
                         "not_a_proof_of_scientific_first_publication": True,
                         "not_a_weak_signal_precision_at_15_test_alone": True,
                         "freeze_before_threshold_tuning": True,
                         "requires_later_independent_article_level_review": True}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return report
