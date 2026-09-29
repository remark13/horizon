"""Deterministic unlabelled diagnostic review sample from the priority pilot.

This is not an independent weak-signal evaluation or a representative
estimate of precision across all SAIA queries.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

from saia.priority_arxiv_pilot import _sha


VERSION = "priority-pilot-relevance-review-v1"
FOLLOWUP_VERSION = "priority-pilot-relevance-review-v2"


def build(*, corpus_dir: Path, catalog_path: Path, cases_path: Path,
          per_case: int = 6, offset_per_case: int = 0) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not 1 <= per_case <= 10:
        raise ValueError("Review sample must be bounded to 1–10 per case")
    if not 0 <= offset_per_case <= 100:
        raise ValueError("Review offset must be bounded to 0–100 per case")
    manifest_path = corpus_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != "priority-arxiv-pilot-corpus-v1":
        raise ValueError("Unknown pilot corpus version")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if _sha(catalog_path) != manifest["inputs"]["catalog_sha256"]:
        raise ValueError("Catalog differs from frozen corpus")
    if _sha(cases_path) != manifest["inputs"]["case_config_sha256"]:
        raise ValueError("Case config differs from frozen corpus")
    config = json.loads(cases_path.read_text(encoding="utf-8"))
    case_ids = {case["id"] for case in config["cases"]}
    labels = {row["id"]: row.get("title_ru") or row.get("title_original")
              for row in catalog["national_search_areas"] + catalog["customer_examples"]}
    assignments = pq.read_table(corpus_dir / "assignments.parquet").to_pylist()
    if len(assignments) != manifest["case_assignments"]:
        raise ValueError("Assignment count differs from corpus manifest")
    by_case = defaultdict(set)
    for item in assignments:
        by_case[item["catalog_id"]].add(item["arxiv_id"])
    selected = []
    seed = manifest["source"]["inventory_sha256"]
    for case_id in sorted(manifest["assignment_counts"]):
        if case_id not in labels:
            raise ValueError("Unknown catalog case in corpus")
        identifiers = sorted(by_case[case_id], key=lambda identifier: sha256(
            f"{seed}\0{case_id}\0{identifier}".encode()).hexdigest())
        selected.extend((case_id, identifier) for identifier in
                        identifiers[offset_per_case:offset_per_case + per_case])
    selected_ids = {identifier for _, identifier in selected}
    documents = pq.read_table(corpus_dir / "documents.parquet",
                              columns=["arxiv_id", "first_submission_date", "title_current"])
    subset = documents.filter(pc.is_in(documents["arxiv_id"],
                                       value_set=pa.array(sorted(selected_ids))))
    by_id = {row["arxiv_id"]: row for row in subset.to_pylist()}
    if set(by_id) != selected_ids:
        raise ValueError("Selected assignment has no matching document")
    items = []
    for case_id, identifier in selected:
        row = by_id[identifier]
        items.append({"item_id": "rel-" + sha256(
            f"{seed}\0{case_id}\0{identifier}".encode()).hexdigest()[:20],
            "target_topic": labels[case_id], "case_role": (
                "customer_supplied_example" if case_id.startswith("customer-") else
                "national_search_area"),
            "document": {"arxiv_id": identifier, "title": row["title_current"],
                         "first_submission_date": row["first_submission_date"],
                         "url": f"https://arxiv.org/abs/{identifier}"},
            "review": {"topical_relevance": None, "evidence_role": None,
                       "rationale": None}})
    selection = {"algorithm": "sha256_seeded_uniform_without_replacement_per_nonempty_case",
                 "per_case_cap": per_case,
                 "items": len(items),
                 "empty_cases": sorted(case_ids - set(by_case))}
    if offset_per_case:
        selection["offset_per_case"] = offset_per_case
        selection["offset_exhausted_cases"] = sorted(
            case_id for case_id, identifiers in by_case.items()
            if len(identifiers) <= offset_per_case)
    limits = {"weak_signal_labels_present": False,
              "precision_can_be_reported_before_completed_reviews": False,
              "represents_all_189_rows": False,
              "query_tuning_independent_holdout": False,
              "source_counts_or_system_rank_shown_to_reviewer": False,
              "abstracts_included": False}
    if offset_per_case:
        limits["offset_followup_is_not_independent_weak_signal_detection"] = True
    return {"version": FOLLOWUP_VERSION if offset_per_case else VERSION,
            "source_manifest_sha256": _sha(manifest_path),
            "catalog_sha256": _sha(catalog_path), "case_config_sha256": _sha(cases_path),
            "selection": selection,
            "items": items,
            "review_choices": {"topical_relevance": ["yes", "partial", "no", "uncertain"],
                               "evidence_role": ["primary_technical_result", "review",
                                                 "application_or_case", "market_or_infrastructure",
                                                 "uncertain"]},
            "limits": limits}
