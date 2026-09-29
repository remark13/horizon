"""Immutable bounded live smoke for UKRI, EU programmes and Hugging Face."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from saia.eu_programme_evidence import EUProgrammeQuery, fetch as fetch_eu
from saia.external_sources import VERSION
from saia.huggingface_evidence import HuggingFaceQuery, fetch as fetch_huggingface
from saia.hybrid import digest
from saia.ukri_evidence import UKRIQuery, fetch as fetch_ukri


def run(topic_id: str, query: str, start: str, end: str) -> dict:
    report = {
        "version": VERSION,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scope": "bounded_connector_availability_smoke_not_signal_evaluation",
        "ukri": fetch_ukri(UKRIQuery(topic_id, query, start, end, end, 10)),
        "eu_programmes": fetch_eu(EUProgrammeQuery(topic_id, query, start, end, end, 10)),
        "huggingface": fetch_huggingface(HuggingFaceQuery(topic_id, query, start, end, end, 8)),
        "scientific_score_modified": False,
        "production_thresholds_modified": False,
        "interpretation": (
            "Successful access proves connector availability only. Grants, programme calls and "
            "AI artefacts are separate observations and do not establish a weak-signal verdict."
        ),
    }
    report["report_payload_sha256"] = digest(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Smoke report is immutable; choose a new output path.")
    report = run(args.topic_id, args.query, args.start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "ukri_status": report["ukri"]["status"],
        "eu_status": report["eu_programmes"]["status"],
        "huggingface_status": report["huggingface"]["status"],
        "sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
