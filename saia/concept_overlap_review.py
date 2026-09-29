"""Freeze a small blind review packet from >=2-group literal candidates."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from saia.arxiv_trigram_index import exact_search
from saia.controlled_collection import sha256_file


VERSION = "ru-concept-overlap-review-packet-v1"


def deterministic_sample(ids: set[str], case_id: str, limit: int) -> list[str]:
    if not 1 <= limit <= 30:
        raise ValueError("Review cap must be 1–30")
    return sorted(ids, key=lambda identifier: (
        hashlib.sha256((case_id + "\0" + identifier).encode()).hexdigest(),
        identifier))[:limit]


def build(*, proposals_path: Path, coverage_path: Path, index_dir: Path,
          output_path: Path, case_ids: list[str], per_case: int = 12) -> dict:
    if output_path.exists():
        raise FileExistsError("Review packet is immutable")
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise ValueError("Choose unique case IDs")
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    if (proposals.get("version") != "ru-concept-plan-diagnostic-v2"
            or coverage.get("version") != "ru-concept-group-coverage-v1"
            or coverage["proposals_sha256"] != sha256_file(proposals_path)
            or coverage["index_manifest_sha256"] != sha256_file(
                index_dir / "manifest.json")):
        raise ValueError("Review packet sources are incompatible")
    proposals_by_id = {row["case_id"]: row for row in proposals["rows"]}
    coverage_by_id = {row["case_id"]: row for row in coverage["rows"]}
    selected = []
    for case_id in case_ids:
        case = proposals_by_id.get(case_id)
        check = coverage_by_id.get(case_id)
        if not case or not check or check["status"] != "complete":
            raise ValueError(f"Case {case_id} has no complete coverage diagnostic")
        supports = Counter()
        for group in case["proposal_unreviewed"]["concept_groups"]:
            result = exact_search(index_dir, {
                "included_terms": group["alternatives_en"], "exclusions": [],
                "date_from": "2000-01-01", "as_of_date": "2026-09-01"})
            supports.update(result["arxiv_ids"])
        candidate_ids = {identifier for identifier, count in supports.items()
                         if count >= 2}
        if len(candidate_ids) != check["overlap"]["two_or_more_group_ids"]:
            raise ValueError("Frozen group overlap changed")
        for identifier in deterministic_sample(candidate_ids, case_id, per_case):
            selected.append({"case_id": case_id, "arxiv_id": identifier,
                             "matched_group_count": supports[identifier]})
    connection = sqlite3.connect(
        f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for row in selected:
            record = connection.execute(
                "SELECT title,abstract,first_submission_date,categories "
                "FROM works WHERE arxiv_id=?", (row["arxiv_id"],)).fetchone()
            if record is None:
                raise ValueError("Indexed candidate missing from works table")
            row.update({"title": record["title"], "abstract": record["abstract"],
                        "first_submission_date": record["first_submission_date"],
                        "categories": record["categories"],
                        "source_url": "https://arxiv.org/abs/" + row["arxiv_id"]})
    finally:
        connection.close()
    report = {"version": VERSION,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "proposals_sha256": sha256_file(proposals_path),
              "coverage_sha256": sha256_file(coverage_path),
              "index_manifest_sha256": sha256_file(index_dir / "manifest.json"),
              "case_ids": case_ids, "per_case_cap": per_case,
              "rows": selected,
              "selection": "SHA256(case_id + NUL + arxiv_id) order among >=2-group candidates",
              "policy": {"packet_is_unlabelled": True,
                         "sample_is_not_random_population_estimate": True,
                         "current_snapshot_abstract_not_historical_v1_text": True,
                         "customer_example_is_not_weak_signal_gold": True,
                         "matched_groups_do_not_prove_relevance": True}}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    return report
