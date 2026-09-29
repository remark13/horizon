"""Run the bounded OpenAlex channel for the frozen 27-case pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from saia.cross_domain_pilot import build_openalex_review_artifacts, collect_openalex_pilot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--pause-seconds", type=float, default=1.5)
    args = parser.parse_args()
    with httpx.Client(
        timeout=30, follow_redirects=True,
        headers={"User-Agent": "SAIA/0.4.34 cross-domain retrieval pilot"},
    ) as client:
        report = collect_openalex_pilot(
            client, limit=args.limit, pause_seconds=args.pause_seconds
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    packet, template = build_openalex_review_artifacts(report)
    for path, value in ((args.packet, packet), (args.template, template)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "succeeded": sum(case["request_status"] == "succeeded" for case in report["cases"]),
        "rate_limited": sum(case["request_status"] == "rate_limited" for case in report["cases"]),
        "not_attempted": sum(case["request_status"].startswith("not_attempted") for case in report["cases"]),
        "review_items": len(packet["items"]),
        "report_sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
