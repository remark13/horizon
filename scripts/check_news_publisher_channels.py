"""Bounded metadata-only connection probes. Never write production observations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from saia.aggregator_evidence import AggregatorQuery, fetch
from saia.external_evidence_store import verify
from saia.news_publishers import MANIFEST_PATH, ROOT, VERSION, publishers

OUT = ROOT / "outputs/news-publisher-directory-0.4.60-2026-09-29"


def probe(identifier: str, timeout: float) -> dict:
    row = publishers()[identifier]
    today = datetime.now(timezone.utc).date()
    query = AggregatorQuery("publisher-directory-connection-probe", identifier,
                            row["probe_query"], today - timedelta(days=365), today, today, 5)
    report = fetch(query, timeout=timeout)
    verify(report)
    return {"source": identifier, "name": row["name"], "domain": row["domain"],
            "basis": "directory_connection_probe", "status": report["status"],
            "observed_count": report["observed_count"], "retrieved_at": report["retrieved_at"],
            "query": report["query"], "http_status": report.get("http_status"),
            "raw_response_bytes_sha256": report.get("raw_response_bytes_sha256"),
            "report_payload_sha256": report["report_payload_sha256"],
            "rejected_publisher_count": report.get("rejected_publisher_count"),
            "candidate_search_run": False, "production_database_written": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3, choices=[1, 2, 3])
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 12:
        parser.error("Connection timeout must be between 1 and 12 seconds.")
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(probe, key, args.timeout) for key in publishers()]
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows) % 10 == 0 or len(rows) == len(futures):
                print(json.dumps({"checked": len(rows), "total": len(futures),
                                  "statuses": dict(Counter(r["status"] for r in rows))}), flush=True)
    rows.sort(key=lambda r: r["source"])
    data = {"directory_version": VERSION, "checked_at": datetime.now(timezone.utc).isoformat(),
            "manifest_sha256": hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
            "publisher_channels": len(rows), "independent_aggregators_added": 0,
            "statuses": dict(Counter(r["status"] for r in rows)), "checks": rows,
            "no_model_calls": True, "production_database_written": False,
            "counts_are_samples_not_full_publication_counts": True}
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path = ROOT / "data/reference/news_publishers/checks.2026-09-29.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "connection-checks.json").write_text(encoded, encoding="utf-8")
    with (OUT / "sources.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Источник", "Группа", "Домен", "Страница", "Описание", "Тип публикации",
                         "Способ получения", "Статус проверки", "Материалов в пробе", "Обращение"])
        for result in rows:
            row = publishers()[result["source"]]
            writer.writerow([row["name"], row["category_label_ru"], row["domain"], row["homepage"],
                             row["trust_comment_ru"], row["source_kind"], "Google News RSS",
                             result["status"], result["observed_count"], result["retrieved_at"]])
    print(json.dumps({"output": str(OUT), "checked": len(rows), "statuses": data["statuses"]}), flush=True)


if __name__ == "__main__":
    main()
