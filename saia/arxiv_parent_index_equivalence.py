"""Compare the opt-in guarded-index audit against sealed full-mirror reports."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.arxiv_parent_index_audit import audit
from saia.controlled_collection import sha256_file


VERSION = "arxiv-parent-index-audit-equivalence-v1"
COMPARABLE_FIELDS = ("counts", "series", "publication_series",
                     "full_period_descriptive", "phrase_series")


def compare(*, mirror_dir: Path, index_dir: Path, reports_dir: Path,
            packages_dir: Path, selected_comparison_path: Path) -> dict:
    selected = json.loads(selected_comparison_path.read_text(encoding="utf-8"))
    if selected.get("version") != "arxiv-parent-selected-index-comparison-v1":
        raise ValueError("Unexpected selected-work comparison version")
    outcomes = []
    for entry in selected["results"]:
        if not entry["exact_match"]:
            continue
        name = entry["report"]
        report_path = reports_dir / name
        if sha256_file(report_path) != entry["report_sha256"]:
            raise ValueError("Previously compared full report changed")
        package = packages_dir / name.removesuffix("-parent-arxiv.json")
        old = json.loads(report_path.read_text(encoding="utf-8"))
        started = perf_counter()
        try:
            new = audit(mirror_dir=mirror_dir,
                        mission_path=package / "mission.json",
                        package_manifest_path=package / "manifest.json",
                        index_dir=index_dir)
            normalized = json.loads(json.dumps(new, ensure_ascii=False))
            differences = [field for field in COMPARABLE_FIELDS
                           if old[field] != normalized[field]]
            error = None
        except (ValueError, OSError, KeyError) as exc:
            differences = list(COMPARABLE_FIELDS)
            error = str(exc)
        outcomes.append({"report": name, "seconds": perf_counter() - started,
                         "differences": differences,
                         "exact_metrics_and_series": not differences,
                         "error": error})
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "selected_comparison_sha256": sha256_file(selected_comparison_path),
            "index_manifest_sha256": sha256_file(index_dir / "manifest.json"),
            "old_reports_checked": len(outcomes),
            "old_reports_equivalent": sum(row["exact_metrics_and_series"] for row in outcomes),
            "outcomes": outcomes,
            "limits": {"old_historical_jobs_not_live_20_minute_benchmark": True,
                       "only_unchanged_sealed_packages_checked": True,
                       "only_same_source_and_all_categories": True,
                       "no_production_route_changed": True}}
