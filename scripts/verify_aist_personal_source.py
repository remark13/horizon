#!/usr/bin/env python3
"""Two published AIST feeds, one bounded download each; no article or model.

Japanese feed is reused in memory for four literal query probes. English
feed is checked only for RSS entry count. Writes a new diagnostic directory,
not a signal dataset, full-text corpus or licence for public republication.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.request import Request, build_opener

from saia.rss_evidence import MAX_RESPONSE_BYTES, RSSQuery, _OfficialRedirects, parse_response, selected_feed

ENGLISH_PUBLISHED_FEED = "https://www.aist.go.jp/ctl/module/mid/185/tid/5789/rss.php"
QUERIES = ("imaging materials", "imaging", "quantum", "материалы для визуализации")


def read_feed(url: str) -> tuple[bytes, dict]:
    request = Request(url, headers={"User-Agent": "SAIA-personal-research-prototype/0.4.54",
                                   "Accept": "application/rss+xml, application/xml"})
    with build_opener(_OfficialRedirects("www.aist.go.jp")).open(request, timeout=10) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        status, resolved = response.status, response.geturl()
    if len(payload) > MAX_RESPONSE_BYTES or b"\x00" in payload or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Unbounded or unsafe RSS response.")
    root = ET.fromstring(payload)
    if root.tag.rsplit("}", 1)[-1] not in {"rss", "feed", "RDF"}:
        raise ValueError("Not an RSS/Atom response.")
    items = sum(node.tag.rsplit("}", 1)[-1] in {"item", "entry"} for node in root.iter())
    return payload, {"url": url, "resolved_url": resolved, "http_status": status, "bytes": len(payload),
                     "raw_response_bytes_sha256": hashlib.sha256(payload).hexdigest(), "feed_item_count": items}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 28))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output directory; preserve previous evidence.")
    end, stamp = args.as_of.isoformat(), datetime.now(timezone.utc).isoformat()
    query = RSSQuery("aist-personal-source-diagnostic", QUERIES[0], "2026-01-01", end, end, "aist_press_rss", 5)
    payload, japanese = read_feed(selected_feed(query)[0])
    _, english = read_feed(ENGLISH_PUBLISHED_FEED)
    reports = [parse_response(RSSQuery(query.topic_id, text, query.start, end, end, query.source, 5), payload, stamp)
               for text in QUERIES]
    summary = {"scope": "personal local RSS reader connection, not labelled weak signals or precision evaluation",
               "retrieved_at": stamp, "as_of": end, "feed_downloads": 2,
               "japanese_published_feed": japanese, "english_published_feed": english,
               "probes": [{"query": report["query"]["text"], "status": report["status"],
                           "observed_count": report["observed_article_count"],
                           "original_titles": [row["title"] for row in report["observations"]],
                           "records_outside_research_release_paths": report["records_outside_research_release_paths"],
                           "records_outside_publication_interval": report["records_outside_publication_interval"],
                           "records_without_publication_date": report["records_without_publication_date"]}
                          for report in reports],
               "stores_raw_feed_body": False, "stores_article_text": False, "stores_images": False,
               "scientific_jobs_started": False, "scientific_score_modified": False,
               "model_input_allowed": False, "model_training_allowed": False,
               "public_republication_allowed": False, "usage_scope": "personal_research",
               "accuracy_improvement_measured": False}
    args.output.mkdir(parents=True)
    (args.output / "checkpoint.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "query-probes.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
