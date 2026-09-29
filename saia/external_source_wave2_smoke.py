"""Immutable bounded live smoke for funding, software and scholar sources."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from saia.external_sources import VERSION
from saia.funding_evidence import FundingQuery, fetch as fetch_funding
from saia.hybrid import digest
from saia.scholar_evidence import ScholarQuery, fetch as fetch_scholar
from saia.software_evidence import SoftwareQuery, fetch as fetch_software


def run(topic_id: str, text_query: str, package_system: str, package_name: str,
        start: str, end: str) -> dict:
    report = {
        "version": VERSION,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scope": "bounded_connector_availability_smoke_not_signal_evaluation",
        "funding": fetch_funding(FundingQuery(topic_id, text_query, start, end, end, 5)),
        "software": fetch_software(SoftwareQuery(
            topic_id, package_system, package_name, start, end, end, 10,
        )),
        "scholar": fetch_scholar(ScholarQuery(topic_id, text_query, start, end, end, 5)),
        "scientific_score_modified": False,
        "production_thresholds_modified": False,
        "interpretation": (
            "A successful response proves only bounded connector access. Funding, package releases "
            "and overlapping bibliography are separate observations, not a weak-signal verdict."
        ),
    }
    report["report_payload_sha256"] = digest(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--package-system", required=True)
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Smoke report is immutable; choose a new output path.")
    report = run(args.topic_id, args.query, args.package_system, args.package_name,
                 args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "funding_status": report["funding"]["status"],
        "software_status": report["software"]["status"],
        "scholar_status": report["scholar"]["status"],
        "sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
