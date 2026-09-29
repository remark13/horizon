"""Create an immutable bounded availability report for patent/news connectors."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from saia.external_sources import VERSION
from saia.hybrid import digest
from saia.news_evidence import NewsQuery, fetch as fetch_news
from saia.patent_evidence import PatentQuery, fetch as fetch_patents


def run(topic_id: str, news_query: str, patent_phrase: str,
        news_start: str, patent_start: str, end: str) -> dict:
    report = {
        "version": VERSION,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "scope": "bounded_connector_availability_smoke_not_signal_evaluation",
        "news": fetch_news(NewsQuery(topic_id, news_query, news_start, end, end, 10)),
        "patents": fetch_patents(PatentQuery(topic_id, patent_phrase, patent_start, end, end, 10)),
        "scientific_score_modified": False,
        "production_thresholds_modified": False,
        "interpretation": (
            "A successful response only proves bounded connector access. Rate limits, missing "
            "credentials and source errors are unknown observations, never zero evidence."
        ),
    }
    report["report_payload_sha256"] = digest(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--news-query", required=True)
    parser.add_argument("--patent-phrase", required=True)
    parser.add_argument("--news-start", required=True)
    parser.add_argument("--patent-start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Smoke report is immutable; choose a new output path.")
    report = run(args.topic_id, args.news_query, args.patent_phrase,
                 args.news_start, args.patent_start, args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"output": str(args.output),
                      "news_status": report["news"]["status"],
                      "patent_status": report["patents"]["status"],
                      "sha256": report["report_payload_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
