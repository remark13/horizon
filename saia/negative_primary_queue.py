"""Build a deterministic preliminary queue for primary-result false positives.

Title patterns are sampling strata, never labels.  Abstracts and exact source
metadata are exported for later independent review.  The queue is development
material only and cannot calibrate production G6 by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

from saia.hybrid import digest
from saia.source_registry import file_sha256


VERSION = "primary-negative-candidate-queue-0.4.13"
SALT = "primary-negative-candidate-sampling-0.4.13"
STRATA = (
    ("review_or_survey_candidate", re.compile(r"\b(review|survey)\b", re.I)),
    ("framework_or_perspective_candidate", re.compile(r"\b(framework|perspective|position paper)\b", re.I)),
    ("benchmark_or_taxonomy_candidate", re.compile(r"\b(benchmark|taxonomy)\b", re.I)),
)


def first_version_date(versions: list[dict] | None) -> str | None:
    if not versions or not versions[0].get("created"):
        return None
    return parsedate_to_datetime(versions[0]["created"]).date().isoformat()


def sampling_stratum(title: str) -> str | None:
    for name, pattern in STRATA:
        if pattern.search(title):
            return name
    return None


def stable_rank(identifier: str) -> str:
    return hashlib.sha256(f"{SALT}:{identifier}".encode()).hexdigest()


def collect_candidates(paths: list[Path], snapshot_revision: str,
                       max_per_stratum: int = 10) -> dict:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pyarrow is required for parquet queue generation.") from exc
    if max_per_stratum < 1:
        raise ValueError("max_per_stratum must be positive.")
    candidates = {name: [] for name, _ in STRATA}
    file_inventory = []
    for path in sorted(paths):
        file_inventory.append({"file": path.name, "bytes_sha256": file_sha256(path)})
        columns = ["id", "title", "abstract", "categories", "versions", "license"]
        for batch in parquet.ParquetFile(path).iter_batches(columns=columns, batch_size=1024):
            for row in batch.to_pylist():
                title = " ".join(str(row.get("title") or "").split())
                abstract = " ".join(str(row.get("abstract") or "").split())
                stratum = sampling_stratum(title)
                if not stratum or not abstract:
                    continue
                identifier = str(row["id"]).strip()
                candidates[stratum].append({
                    "arxiv_id": identifier,
                    "arxiv_url": f"https://arxiv.org/abs/{identifier}",
                    "title": title,
                    "abstract": abstract,
                    "categories": sorted(str(row.get("categories") or "").split()),
                    "first_submission_date": first_version_date(row.get("versions")),
                    "article_license": row.get("license"),
                    "source_file": path.name,
                    "sampling_stratum": stratum,
                    "sampling_reason": "title_pattern_for_manual_primary_result_review",
                    "primary_result_label": None,
                    "weak_signal_label": None,
                })
    selected = []
    available = {}
    for stratum, values in candidates.items():
        available[stratum] = len(values)
        values.sort(key=lambda item: (stable_rank(item["arxiv_id"]), item["arxiv_id"]))
        selected.extend(values[:max_per_stratum])
    selected.sort(key=lambda item: (item["sampling_stratum"], stable_rank(item["arxiv_id"])))
    if len({item["arxiv_id"] for item in selected}) != len(selected):
        raise ValueError("Duplicate work selected into multiple strata.")
    report = {
        "version": VERSION,
        "partition": "development_candidate_negative_queue",
        "not_gold_standard": True,
        "independent_review_completed": False,
        "production_g6_calibration_allowed": False,
        "source": {
            "kind": "local_arxiv_selected_parquet",
            "snapshot_revision": snapshot_revision,
            "file_inventory": file_inventory,
            "file_inventory_sha256": digest(file_inventory),
            "metadata_license": "CC0 according to cached dataset card; article rights are separate",
        },
        "sampling": {
            "version": SALT,
            "max_per_stratum": max_per_stratum,
            "available_by_stratum": available,
            "selected_by_stratum": dict(sorted(Counter(item["sampling_stratum"] for item in selected).items())),
            "title_patterns_are_labels": False,
        },
        "items": selected,
        "limitations": [
            "Review, framework and benchmark words are sampling hints and can occur in original research.",
            "The queue is drawn from the current narrow phrase scope, not the whole AI literature.",
            "One agent created the queue; labels require independent human review.",
            "No reserved holdout text was opened or evaluated.",
        ],
    }
    report["report_payload_sha256"] = digest(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet-dir", type=Path, required=True)
    parser.add_argument("--snapshot-revision", required=True)
    parser.add_argument("--max-per-stratum", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Queue is immutable; choose a new output path.")
    paths = sorted(args.parquet_dir.glob("*.parquet"))
    if not paths:
        parser.error("No parquet inputs found.")
    report = collect_candidates(paths, args.snapshot_revision, args.max_per_stratum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "selected": len(report["items"]),
        "selected_by_stratum": report["sampling"]["selected_by_stratum"],
        "sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
