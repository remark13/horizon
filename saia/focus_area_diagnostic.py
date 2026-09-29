"""Bounded availability diagnostic for the controlled focus-area catalog.

The diagnostic deliberately does not assign relevance or weak-signal labels.
It only records whether the publication preview is reachable and what the
bounded response contains.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from saia.focus_areas import catalog


def _post_json(url: str, payload: dict, timeout: int = 90) -> dict:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def summarize(profile: dict, response: dict) -> dict:
    works = response.get("works") or []
    return {
        "profile_id": profile["id"],
        "name_ru": profile["name_ru"],
        "query_en": profile["starter_query_en"],
        "query_hash": response.get("query_hash"),
        "canonical_works_in_bounded_preview": len(works),
        "source_counts": response.get("source_counts") or {},
        "source_errors": response.get("errors") or {},
        "limitations": response.get("limitations") or [],
        "sample_works": [
            {
                "title": item.get("title"),
                "published_at": item.get("published_at"),
                "sources": item.get("sources") or [],
                "urls": item.get("urls") or [],
            }
            for item in works[:10]
        ],
        "manual_relevance_review_required": True,
        "weak_signal_assessment_performed": False,
    }


def run(base_url: str, date_from: date, as_of: date, limit: int,
        pause_seconds: float = 3.1) -> dict:
    entries = []
    for profile in catalog()["profiles"]:
        request_payload = {
            "query": profile["starter_query_en"],
            "date_from": date_from.isoformat(),
            "as_of_date": as_of.isoformat(),
            "limit_per_source": limit,
        }
        started = time.monotonic()
        try:
            response = _post_json(f"{base_url.rstrip('/')}/discover/preview", request_payload)
            entry = summarize(profile, response)
            entry["request_status"] = "succeeded"
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            entry = {
                "profile_id": profile["id"],
                "name_ru": profile["name_ru"],
                "query_en": profile["starter_query_en"],
                "request_status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
                "manual_relevance_review_required": True,
                "weak_signal_assessment_performed": False,
            }
        entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
        entries.append(entry)
        time.sleep(pause_seconds)
    report = {
        "version": "focus-area-base-query-smoke-0.4.32",
        "created_at": date.today().isoformat(),
        "input": {
            "base_url": base_url,
            "date_from": date_from.isoformat(),
            "as_of_date": as_of.isoformat(),
            "limit_per_source": limit,
            "focus_area_catalog_version": catalog()["version"],
        },
        "interpretation": (
            "Ограниченный предпросмотр проверяет доступность источников и наблюдаемость "
            "широкого запроса. Он не измеряет полноту, релевантность или качество "
            "детектора слабых сигналов."
        ),
        "entries": entries,
        "summary": {
            "profiles": len(entries),
            "requests_succeeded": sum(item["request_status"] == "succeeded" for item in entries),
            "requests_failed": sum(item["request_status"] == "failed" for item in entries),
            "profiles_with_source_errors": sum(bool(item.get("source_errors")) for item in entries),
            "bounded_preview_works": sum(
                item.get("canonical_works_in_bounded_preview", 0) for item in entries
            ),
        },
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_payload_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report
