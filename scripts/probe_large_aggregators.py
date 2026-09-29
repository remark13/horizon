#!/usr/bin/env python3
"""Small live probes; no scraping, keys, full bodies or kernel recalculation."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from saia.aggregator_evidence import AggregatorQuery, fetch
from saia.news_evidence import NewsQuery, fetch as fetch_news


def main():
    today = datetime.now(timezone.utc).date()
    reports = []
    for source, text in (("google_news_rss", "robotics"), ("google_news_rss", "беспилотные авиационные системы"),
                         ("dealroom_marketmaps", "AI"), ("dealroom_marketmaps", "robotics"),
                         ("dealroom_marketmaps", "battery"), ("dealroom_public_rounds", "robotics")):
        report = fetch(AggregatorQuery("aggregator-probe:" + source + ":" + text, source, text,
                       today - timedelta(days=365), today, today, 5))
        reports.append(report)
        print(json.dumps({"source": source, "query": text, "status": report["status"], "count": report.get("observed_count"),
                          "provider_cache_status": report.get("provider_cache_status")}, ensure_ascii=False), flush=True)
    report = fetch_news(NewsQuery("aggregator-probe:gdelt:robotics", "robotics", today - timedelta(days=29), today, today, 5), timeout=12)
    reports.append(report)
    print(json.dumps({"source": "gdelt_doc_2_0", "status": report["status"], "count": report.get("observed_article_count")}), flush=True)
    for source in ("event_registry", "mediacloud_news", "lens_patents"):
        report = fetch(AggregatorQuery("aggregator-probe:" + source, source, "robotics", today - timedelta(days=29), today, today, 5))
        reports.append(report)
        print(json.dumps({"source": source, "status": report["status"], "count": report.get("observed_count")}), flush=True)
    target = Path(__file__).resolve().parents[1] / "outputs/large-aggregators-0.4.55-2026-09-29/probes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"retrieved_at": datetime.now(timezone.utc).isoformat(), "reports": reports,
                                 "scientific_results_modified": False, "precision_improvement_proven": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(target), flush=True)


if __name__ == "__main__":
    main()
