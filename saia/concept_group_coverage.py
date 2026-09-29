"""Read-only diagnostic of loss from intersecting literal concept groups.

The union is a candidate *pool*, not a relevance-qualified publication set.
This tool never changes the user-facing retrieval policy.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.arxiv_trigram_index import exact_search, supports_plan
from saia.controlled_collection import sha256_file


VERSION = "ru-concept-group-coverage-v1"
MAX_GROUP_IDS = 100_000


def overlap_counts(group_ids: list[set[str]]) -> dict:
    if not group_ids:
        raise ValueError("At least one concept group is required")
    support = Counter(identifier for group in group_ids for identifier in group)
    count = len(group_ids)
    return {"any_group_candidate_ids": len(support),
            "two_or_more_group_ids": sum(value >= 2 for value in support.values()),
            "all_group_literal_ids": sum(value == count for value in support.values())}


def probe(*, proposals_path: Path, index_dir: Path, output_path: Path,
          max_cases: int | None = None) -> dict:
    if output_path.exists():
        raise FileExistsError("Coverage diagnostic is immutable")
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    if proposals.get("version") not in {"ru-concept-plan-diagnostic-v1",
                                         "ru-concept-plan-diagnostic-v2"}:
        raise ValueError("Unknown frozen concept proposal format")
    cases = proposals["rows"]
    if max_cases is not None:
        if not 1 <= max_cases <= len(cases):
            raise ValueError("Invalid case cap")
        cases = cases[:max_cases]
    rows = []
    for item in cases:
        row = {"case_id": item["case_id"], "role": item["role"],
               "query_ru": item["query_ru"]}
        if item["status"] != "parsed":
            row.update({"status": "model_parse_failed", "groups": []})
            rows.append(row)
            continue
        rows_of_ids = []
        group_rows = []
        for number, group in enumerate(item["proposal_unreviewed"]["concept_groups"], 1):
            phrases = group["alternatives_en"]
            group_row = {"number": number, "source_span_ru": group["source_span_ru"],
                         "alternatives_en": phrases}
            plan = {"included_terms": phrases, "exclusions": [],
                    "date_from": "2000-01-01", "as_of_date": "2026-09-01"}
            if not supports_plan(plan):
                group_row.update({"status": "unsupported_literal_plan",
                                  "candidate_ids": None})
                group_rows.append(group_row)
                continue
            began = perf_counter()
            try:
                result = exact_search(index_dir, plan)
            except ValueError as error:
                if str(error) != "Indexed query intersects duplicate source IDs":
                    raise
                group_row.update({"status": "duplicate_guard_requires_full_scan",
                                  "candidate_ids": None})
            else:
                ids = set(result["arxiv_ids"])
                if len(ids) > MAX_GROUP_IDS:
                    group_row.update({"status": "too_broad_for_bounded_diagnostic",
                                      "candidate_ids": len(ids)})
                else:
                    group_row.update({"status": "counted", "candidate_ids": len(ids)})
                    rows_of_ids.append(ids)
            group_row["seconds"] = perf_counter() - began
            group_rows.append(group_row)
        row["groups"] = group_rows
        if len(rows_of_ids) == len(group_rows):
            row.update({"status": "complete",
                        "overlap": overlap_counts(rows_of_ids)})
        else:
            row.update({"status": "partial_uncomparable", "overlap": None})
        row["proposal_structural_issues"] = item["structural_validation"]["issues"]
        rows.append(row)
    report = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
              "proposals_sha256": sha256_file(proposals_path),
              "index_manifest_sha256": sha256_file(index_dir / "manifest.json"),
              "window": {"from": "2000-01-01", "as_of_exclusive": "2026-09-01"},
              "limits": {"max_group_ids": MAX_GROUP_IDS}, "rows": rows,
              "status_counts": {status: sum(row["status"] == status for row in rows)
                                for status in sorted({row["status"] for row in rows})},
              "policy": {"query_formulations_are_unreviewed": True,
                         "candidate_union_is_not_relevance_or_weak_signal_detection": True,
                         "group_intersection_is_only_literal_matching": True,
                         "index_is_fixed_current_text_snapshot": True}}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    return report
