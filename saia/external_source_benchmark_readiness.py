"""Audit whether the 100-signal seed registry can support a fair source benchmark."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from saia.hybrid import digest


REQUIRED_QUERY_KEYS = ("ukri_query_en", "eu_query_en", "huggingface_query_en")


def audit(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("Seed registry is empty.")
    explicit = {key: sum(bool(row.get(key)) for row in rows) for key in REQUIRED_QUERY_KEYS}
    report = {
        "version": "external-source-benchmark-readiness-0.4.31",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(path),
        "records": len(rows),
        "areas": dict(sorted(Counter(str(row.get("area") or "unknown") for row in rows).items())),
        "records_with_gold_label": sum(row.get("label") is not None for row in rows),
        "records_with_label_as_of": sum(row.get("label_as_of") is not None for row in rows),
        "records_with_explicit_source_queries": explicit,
        "benchmark_ready": all(value == len(rows) for value in explicit.values())
                           and all(row.get("label") is not None and row.get("label_as_of") for row in rows),
        "benchmark_executed": False,
        "scientific_score_modified": False,
        "reasons": [
            "The registry is a seed list, not an independently labelled gold set.",
            "UKRI, EU and Hugging Face need controlled English queries; generating them from known outcomes would introduce target leakage.",
            "Per-signal as-of labels are required before source confirmations can be scored as true or false positives.",
        ],
        "next_required_artifact": (
            "A blinded query manifest with signal_id, query_en, query_version, as_of_date, "
            "allowed aliases and reviewer approval, followed by a capped stratified pilot."
        ),
    }
    report["report_payload_sha256"] = digest(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Readiness report is immutable; choose a new output path.")
    report = audit(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"output": str(args.output), "records": report["records"],
                      "benchmark_ready": report["benchmark_ready"],
                      "sha256": report["report_payload_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
