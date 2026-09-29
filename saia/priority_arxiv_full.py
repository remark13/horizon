"""One-pass diagnostic over the complete pinned local arXiv mirror.

This is seeded *retrieval*, not signal detection. It does not produce a field
denominator or use present metadata as historical as-of text.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path

from saia.controlled_collection import inventory_sha256
from saia.local_arxiv_search import policy, validate_inventory
from saia.priority_arxiv_pilot import _boundary_pattern, _sha, _validated_cases


VERSION = "priority-arxiv-full-pilot-v1"
MAX_OUTPUT_BYTES = 5 * 1024 * 1024
MIN_FREE_BYTES = 50 * 1024 ** 3


def _retain(state: dict, reference: dict, earliest_cap: int, latest_cap: int) -> None:
    state["earliest"].append(reference)
    state["earliest"].sort(key=lambda item: (item["first_submission"], item["arxiv_id"]))
    del state["earliest"][earliest_cap:]
    state["latest"].append(reference)
    state["latest"].sort(key=lambda item: (item["first_submission"], item["arxiv_id"]), reverse=True)
    del state["latest"][latest_cap:]


def _existing_parent(path: Path) -> Path:
    """Find a filesystem-backed parent without creating directories."""
    parent = path.parent
    while not parent.exists():
        if parent == parent.parent:
            raise FileNotFoundError("No existing parent for pilot report")
        parent = parent.parent
    return parent


def run(*, catalog_path: Path, case_config_path: Path, mirror_dir: Path,
        cache_manifest_path: Path, output_path: Path,
        date_from: date = date(2000, 1, 1), as_of_date: date = date(2026, 9, 1),
        max_examples_per_case: int = 20) -> dict:
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from saia.arxiv_metadata import first_submission
    import shutil

    if date_from >= as_of_date or not 2 <= max_examples_per_case <= 30:
        raise ValueError("Invalid pilot period or example cap")
    if shutil.disk_usage(_existing_parent(output_path)).free < MIN_FREE_BYTES:
        raise OSError("Insufficient free disk reserve for full-mirror pilot")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    config = json.loads(case_config_path.read_text(encoding="utf-8"))
    if config.get("version") != "priority-arxiv-pilot-v2":
        raise ValueError("Full mirror comparison requires the frozen v2 exact terms")
    cases = _validated_cases(config, catalog)
    cfg = policy()
    files = validate_inventory(str(mirror_dir.resolve()), cfg["expected_files"], cfg["expected_rows"])
    inventory_hash = inventory_sha256(files, cfg["expected_rows"], cfg)
    cache_manifest = json.loads(cache_manifest_path.read_text(encoding="utf-8"))
    if inventory_hash != cache_manifest["source"]["inventory_sha256"]:
        raise ValueError("Mirror differs from the previously pinned cache inventory")
    fingerprints = {"catalog_sha256": _sha(catalog_path), "case_config_sha256": _sha(case_config_path),
                    "arxiv_inventory_sha256": inventory_hash}
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if (existing.get("input_fingerprints") != fingerprints
                or existing.get("period") != {"from": date_from.isoformat(),
                                               "as_of_exclusive": as_of_date.isoformat()}
                or existing.get("resource_limits", {}).get("max_example_references_per_case")
                != max_examples_per_case):
            raise FileExistsError("Full-mirror pilot inputs changed; choose a new output path")
        return existing

    states = {case["id"]: {"text_matches": 0, "invalid_first_date": 0,
                            "outside_period": 0, "duplicate_id": 0,
                            "years": Counter(), "seen": set(), "earliest": [], "latest": []}
              for case in cases}
    scanned = 0
    earliest_cap = min(5, max_examples_per_case // 2)
    latest_cap = max_examples_per_case - earliest_cap
    patterns = {case["id"]: [_boundary_pattern(term) for term in case["all_terms"]] for case in cases}
    for path in files:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=8192, columns=["id", "title", "abstract", "versions"]):
            scanned += batch.num_rows
            title, abstract = batch.column(1), batch.column(2)
            term_masks = {}
            for case in cases:
                mask = None
                for pattern in patterns[case["id"]]:
                    if pattern not in term_masks:
                        term_masks[pattern] = pc.or_(
                            pc.fill_null(pc.match_substring_regex(title, pattern, ignore_case=True), False),
                            pc.fill_null(pc.match_substring_regex(abstract, pattern, ignore_case=True), False),
                        )
                    mask = term_masks[pattern] if mask is None else pc.and_(mask, term_masks[pattern])
                state = states[case["id"]]
                for row in batch.filter(mask).to_pylist():
                    state["text_matches"] += 1
                    identifier = str(row.get("id") or "")
                    try:
                        first_date = first_submission(row.get("versions"))
                        published = date.fromisoformat(first_date)
                    except (TypeError, ValueError, IndexError):
                        state["invalid_first_date"] += 1
                        continue
                    if not date_from <= published < as_of_date:
                        state["outside_period"] += 1
                        continue
                    if identifier in state["seen"]:
                        state["duplicate_id"] += 1
                        continue
                    state["seen"].add(identifier)
                    state["years"][str(published.year)] += 1
                    _retain(state, {"arxiv_id": identifier, "first_submission": first_date,
                                    "title_in_current_snapshot": row.get("title"),
                                    "url": f"https://arxiv.org/abs/{identifier}",
                                    "source_shard": path.name}, earliest_cap, latest_cap)
    if scanned != cfg["expected_rows"]:
        raise ValueError("Scan row count differs from pinned inventory")
    result_cases = []
    for case in cases:
        state = states[case["id"]]
        references = list({row["arxiv_id"]: row for row in state["earliest"] + state["latest"]}.values())
        result_cases.append({"catalog_id": case["id"], "terms_and": case["all_terms"],
                             "text_matches_before_date_filter": state["text_matches"],
                             "invalid_first_date": state["invalid_first_date"],
                             "outside_period": state["outside_period"],
                             "duplicate_arxiv_id_excluded": state["duplicate_id"],
                             "eligible_unique_in_pinned_mirror": len(state["seen"]),
                             "year_counts": dict(sorted(state["years"].items())),
                             "example_references": references,
                             "interpretation": "seeded literal retrieval only; not signal detection"})
    report = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
              "input_fingerprints": fingerprints,
              "source": {"dataset": cfg["dataset"], "revision": cfg["revision"],
                         "inventory_files": len(files), "inventory_rows": cfg["expected_rows"]},
              "period": {"from": date_from.isoformat(), "as_of_exclusive": as_of_date.isoformat()},
              "scanned_rows": scanned, "cases": result_cases,
              "coverage": {"full_pinned_mirror_inventory_scanned": True,
                           "all_arxiv_coverage_proven": False,
                           "full_openalex_coverage": False,
                           "technology_field_denominator_available": False,
                           "publication_share_growth_measured": False,
                           "weak_signal_detected": False,
                           "historical_title_abstract_frozen_at_first_submission": False,
                           "zero_means_no_literal_match_in_pinned_mirror_only": True},
              "resource_limits": {"max_output_bytes": MAX_OUTPUT_BYTES,
                                  "min_free_disk_reserve_bytes": MIN_FREE_BYTES,
                                  "max_example_references_per_case": max_examples_per_case}}
    serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(serialized.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("Pilot report exceeds output cap")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(serialized, encoding="utf-8")
    return report
