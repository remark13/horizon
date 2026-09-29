#!/usr/bin/env python3
"""Bounded source/UI smoke, not accuracy evaluation or a corpus harvest.

Calls only the local SAIA API. The API queries its already registered sources
and saves metadata observations. No scientific jobs are launched or modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PROBES = (
    ("mit_news_rss", "/external-evidence/news/rss", "robot", {"source": "mit_news_rss"}),
    ("nasa_news_rss", "/external-evidence/news/rss", "flight", {"source": "nasa_news_rss"}),
    ("jpl_news_rss", "/external-evidence/news/rss", "robot", {"source": "jpl_news_rss"}),
    ("gdelt_doc_2_0", "/external-evidence/news/gdelt", "robot", {"recent_days": 30}),
    ("epo_ops", "/external-evidence/patents/epo-ops", "autonomous flight", {"phrase": "autonomous flight"}),
    ("ukri_gtr", "/external-evidence/funding/ukri", "autonomous flight", {}),
    ("usaspending", "/external-evidence/procurement/usaspending", "autonomous flight", {}),
    ("nasa_ntrs", "/external-evidence/aerospace/nasa-ntrs", "autonomous flight", {}),
    ("huggingface_hub", "/external-evidence/ai-artifacts/hugging-face", "reinforcement learning", {}),
    ("semantic_scholar", "/external-evidence/scholar/semantic-scholar", "reinforcement learning", {}),
    ("eu_funding_tenders", "/external-evidence/programmes/eu-funding", "artificial intelligence", {}),
    ("clinicaltrials_gov", "/external-evidence/clinical-trials/clinicaltrials-gov", "gene therapy", {}),
    ("europe_pmc", "/external-evidence/biomedical/europe-pmc", "gene therapy", {}),
    ("nih_reporter", "/external-evidence/funding/nih-reporter", "gene therapy", {}),
    ("deps_dev", "/external-evidence/software/deps-dev", "torch", {"system": "pypi", "package": "torch", "max_versions": 5}),
)


def api(base: str, path: str, payload: dict | None = None) -> dict:
    request = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json"}, method="POST" if payload is not None else "GET")
    with urlopen(request, timeout=55) as response:
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("Local API response exceeds bound")
    return json.loads(raw)


def checksum(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def probe(base: str, today: date, spec: tuple) -> dict:
    source, endpoint, query, extra = spec
    extra = dict(extra)
    start = today - timedelta(days=extra.pop("recent_days", 5 * 366))
    payload = {"topic_id": "sources-ui-live-smoke-" + today.isoformat(), "query": query,
               "start": start.isoformat(), "end": today.isoformat(), "as_of": today.isoformat(),
               "max_records": 5, "save": True, **extra}
    if source == "epo_ops":
        payload.pop("query")
    if source == "deps_dev":
        payload.pop("query")
        payload.pop("max_records")
    began = time.monotonic()
    try:
        report = api(base, endpoint, payload)
        rows = report.get("observations")
        sample = [{"title": row.get("title") or row.get("repo_id") or row.get("package"),
                   "url": row.get("url"), "author": row.get("author"),
                   "publisher": row.get("publisher"),
                   "record_date": next((row[k] for k in ("published_at", "publication_date", "first_posted_date",
                        "project_start_date", "project_start", "start_date", "created_at", "year") if row.get(k)), None)}
                  for row in rows[:2]] if isinstance(rows, list) else []
        return {"source": source, "query": query, "status": report.get("status"),
                "observed_count": len(rows) if isinstance(rows, list) else None,
                "sample": sample, "elapsed_seconds": round(time.monotonic() - began, 3),
                "saved_observation_id": report.get("saved", {}).get("observation_id"),
                "scientific_score_modified": report.get("scientific_score_modified", False),
                "coverage_exhaustive": False}
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        # Do not export an arbitrary exception body, URLs with keys or env vars.
        return {"source": source, "query": query, "status": "local_probe_failed",
                "observed_count": None, "error_type": type(error).__name__,
                "elapsed_seconds": round(time.monotonic() - began, 3), "coverage_exhaustive": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = urlsplit(args.base)
    if target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost"} or target.username or target.path:
        raise ValueError("Use only a local SAIA API origin")
    today = datetime.now(timezone.utc).date()
    card_path = "/signals/universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915?score_run_id=9509"
    before = checksum(api(args.base, card_path))
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda spec: probe(args.base, today, spec), PROBES))
    after = checksum(api(args.base, card_path))
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "health": api(args.base, "/health"),
              "probe_scope": "15 registered connectors; no full scientific pipeline or accuracy test",
              "sources": rows, "scientific_cards_before_sha256": before,
              "scientific_cards_after_sha256": after, "scientific_cards_unchanged": before == after,
              "limitations": ["Bounded samples are not complete source coverage.",
                  "Search matches are not verified evidence of a weak signal.",
                  "A source error is unknown, not zero.", "Grants/contracts are not private investments."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "sources": [{k: row[k] for k in
        ("source", "status", "observed_count", "elapsed_seconds")} for row in rows],
        "scientific_cards_unchanged": before == after}, ensure_ascii=False))
    if before != after:
        raise SystemExit("Scientific cards changed during source-only smoke")


if __name__ == "__main__":
    main()
