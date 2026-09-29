"""External-attention observations kept separate from scientific emergence.

Wikimedia Pageviews can observe public attention from 2015-07-01 onward.
GDELT DOC 2.0 is a rolling recent-window service and must not be used as a
historical 2010--2017 archive.  Missing observations are never converted to 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from saia.hybrid import digest


VERSION = "external-attention-evidence-0.4.23"
WIKIMEDIA_EARLIEST = date(2015, 7, 1)
GDELT_DOC_MAX_LOOKBACK_DAYS = 93


def _date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


@dataclass(frozen=True)
class WikimediaMapping:
    topic_id: str
    article_title: str
    project: str = "en.wikipedia.org"
    mapping_method: str = "manual_explicit"
    candidate_titles: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.topic_id.strip() or not self.article_title.strip():
            raise ValueError("Topic ID and explicit Wikimedia article are required.")
        candidates = {value.strip() for value in self.candidate_titles if value.strip()}
        if candidates and (len(candidates) != 1 or self.article_title not in candidates):
            raise ValueError("Ambiguous Wikimedia mapping must be resolved before collection.")
        if (
            "://" in self.project
            or "/" in self.project
            or not self.project.endswith((".wikipedia.org", ".wikimedia.org"))
        ):
            raise ValueError("Wikimedia project must be an explicit Wikimedia host.")


def wikimedia_resolution_url(mapping: WikimediaMapping) -> str:
    """Build an exact-title MediaWiki query that normalizes redirects first."""
    mapping.validate()
    query = urlencode({
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "redirects": 1,
        "titles": mapping.article_title,
    })
    return f"https://{mapping.project}/w/api.php?{query}"


def parse_wikimedia_resolution(
    mapping: WikimediaMapping,
    payload_bytes: bytes,
    retrieved_at: str,
    request_url: str,
    final_url: str | None = None,
    http_status: int = 200,
) -> dict:
    """Parse exact-title resolution without substituting a search result."""
    mapping.validate()
    payload = json.loads(payload_bytes)
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list) or len(pages) != 1:
        raise ValueError("MediaWiki resolution must contain exactly one page.")
    page = pages[0]
    if not isinstance(page, dict) or not isinstance(page.get("title"), str):
        raise ValueError("Invalid MediaWiki resolution page.")
    normalized = query.get("normalized") or []
    redirects = query.get("redirects") or []
    if not isinstance(normalized, list) or not isinstance(redirects, list):
        raise ValueError("Invalid MediaWiki normalization metadata.")
    missing = bool(page.get("missing") is True or "missing" in page)
    invalid = bool(page.get("invalid") is True or "invalid" in page)
    if invalid:
        raise ValueError("MediaWiki rejected the requested article title.")
    resolved_title = None if missing else page["title"]
    result = {
        "version": VERSION,
        "source": "mediawiki_action_api",
        "status": "not_found" if missing else "resolved",
        "requested_article_title": mapping.article_title,
        "resolved_article_title": resolved_title,
        "page_id": None if missing else page.get("pageid"),
        "normalized": normalized,
        "redirects": redirects,
        "redirect_resolved": bool(redirects),
        "request": {
            "url": request_url,
            "final_url": final_url,
            "http_status": http_status,
        },
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    result["report_payload_sha256"] = digest(result)
    return result


def resolve_wikimedia(mapping: WikimediaMapping, timeout: float = 30.0) -> dict:
    url = wikimedia_resolution_url(mapping)
    request = Request(url, headers={"User-Agent": "SAIA-research-prototype/0.4.23"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
        return parse_wikimedia_resolution(
            mapping,
            payload,
            datetime.now(timezone.utc).isoformat(),
            request_url=url,
            final_url=response.geturl(),
            http_status=response.status,
        )


def wikimedia_url(mapping: WikimediaMapping, start: str | date, end: str | date,
                  access: str = "all-access", agent: str = "user",
                  granularity: str = "daily",
                  article_title: str | None = None) -> str:
    mapping.validate()
    start_day, end_day = _date(start), _date(end)
    if start_day < WIKIMEDIA_EARLIEST:
        raise ValueError("Wikimedia Pageviews starts on 2015-07-01.")
    if end_day < start_day:
        raise ValueError("End date precedes start date.")
    title = quote((article_title or mapping.article_title).replace(" ", "_"), safe="")
    return (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        f"{quote(mapping.project, safe='')}/{access}/{agent}/{title}/{granularity}/"
        f"{start_day:%Y%m%d}/{end_day:%Y%m%d}"
    )


def parse_wikimedia(mapping: WikimediaMapping, start: str | date, end: str | date,
                    payload_bytes: bytes, retrieved_at: str,
                    request_url: str | None = None,
                    final_url: str | None = None,
                    http_status: int = 200,
                    resolution: dict | None = None) -> dict:
    mapping.validate()
    start_day, end_day = _date(start), _date(end)
    payload = json.loads(payload_bytes)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("Wikimedia response has no items list.")
    observations = []
    for item in items:
        timestamp = str(item.get("timestamp", ""))
        if len(timestamp) < 8 or not isinstance(item.get("views"), int):
            raise ValueError("Invalid Wikimedia observation.")
        day = datetime.strptime(timestamp[:8], "%Y%m%d").date()
        if not start_day <= day <= end_day:
            raise ValueError("Wikimedia observation is outside requested interval.")
        observations.append({"date": day.isoformat(), "views": item["views"]})
    by_day = {item["date"]: item["views"] for item in observations}
    if len(by_day) != len(observations):
        raise ValueError("Duplicate Wikimedia day in response.")
    expected_days = []
    current = start_day
    while current <= end_day:
        expected_days.append(current.isoformat())
        current += timedelta(days=1)
    missing_days = [day for day in expected_days if day not in by_day]
    result = {
        "version": VERSION,
        "source": "wikimedia_pageviews",
        "role": "external_attention_only",
        "mapping": {
            "topic_id": mapping.topic_id,
            "article_title": mapping.article_title,
            "resolved_article_title": (
                resolution.get("resolved_article_title") if resolution
                else mapping.article_title
            ),
            "project": mapping.project,
            "mapping_method": mapping.mapping_method,
            "redirect_resolved": bool(resolution and resolution.get("redirect_resolved")),
            "transport_redirected": bool(request_url and final_url and request_url != final_url),
            "ambiguity_resolved": True,
        },
        "title_resolution": resolution,
        "request": {"start": start_day.isoformat(), "end": end_day.isoformat(),
                    "url": request_url, "final_url": final_url,
                    "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if not missing_days else "partial",
        "missing_days": missing_days,
        "observations": sorted(observations, key=lambda item: item["date"]),
        "total_views_observed": sum(by_day.values()) if by_day else None,
        "missing_is_zero": False,
        "scientific_score_modified": False,
    }
    result["report_payload_sha256"] = digest(result)
    return result


def wikimedia_not_found(mapping: WikimediaMapping, start: str | date, end: str | date,
                        payload_bytes: bytes, retrieved_at: str,
                        request_url: str | None,
                        http_status: int | None = 404,
                        resolution: dict | None = None) -> dict:
    mapping.validate()
    result = {
        "version": VERSION,
        "source": "wikimedia_pageviews",
        "role": "external_attention_only",
        "mapping": {
            "topic_id": mapping.topic_id,
            "article_title": mapping.article_title,
            "resolved_article_title": (
                resolution.get("resolved_article_title") if resolution else None
            ),
            "project": mapping.project,
            "mapping_method": mapping.mapping_method,
            "redirect_resolved": bool(resolution and resolution.get("redirect_resolved")),
            "transport_redirected": False,
            "ambiguity_resolved": True,
        },
        "title_resolution": resolution,
        "request": {"start": _date(start).isoformat(), "end": _date(end).isoformat(),
                    "url": request_url, "final_url": None,
                    "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": (
            hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None
        ),
        "status": (
            "not_found"
            if resolution and resolution.get("status") == "not_found"
            else "unavailable_or_zero"
        ),
        "status_reason": (
            "title_resolution_not_found"
            if resolution and resolution.get("status") == "not_found"
            else "pageviews_404_cannot_distinguish_zero_from_unloaded_data"
        ),
        "missing_days": None,
        "observations": None,
        "total_views_observed": None,
        "missing_is_zero": False,
        "scientific_score_modified": False,
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch_wikimedia(mapping: WikimediaMapping, start: str | date, end: str | date,
                    timeout: float = 30.0) -> dict:
    resolution = resolve_wikimedia(mapping, timeout=timeout)
    if resolution["status"] == "not_found":
        return wikimedia_not_found(
            mapping,
            start,
            end,
            b"",
            datetime.now(timezone.utc).isoformat(),
            None,
            http_status=None,
            resolution=resolution,
        )
    resolved_title = resolution["resolved_article_title"]
    url = wikimedia_url(mapping, start, end, article_title=resolved_title)
    request = Request(url, headers={"User-Agent": "SAIA-research-prototype/0.4.23"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
            final_url = response.geturl()
            status = response.status
    except HTTPError as exc:
        payload = exc.read()
        if exc.code == 404:
            return wikimedia_not_found(
                mapping, start, end, payload, datetime.now(timezone.utc).isoformat(),
                url, http_status=exc.code, resolution=resolution,
            )
        raise
    return parse_wikimedia(
        mapping, start, end, payload,
        datetime.now(timezone.utc).isoformat(), request_url=url,
        final_url=final_url, http_status=status, resolution=resolution,
    )


def gdelt_doc_assessment(start: str | date, end: str | date,
                         as_of: str | date) -> dict:
    start_day, end_day, as_of_day = _date(start), _date(end), _date(as_of)
    if end_day < start_day or end_day > as_of_day:
        raise ValueError("Invalid GDELT interval.")
    earliest = as_of_day - timedelta(days=GDELT_DOC_MAX_LOOKBACK_DAYS)
    blocked = start_day < earliest
    result = {
        "version": VERSION,
        "source": "gdelt_doc_2_0",
        "role": "external_attention_only",
        "request": {"start": start_day.isoformat(), "end": end_day.isoformat(),
                    "as_of": as_of_day.isoformat()},
        "documented_window": {"kind": "rolling_recent", "max_lookback_days": 93,
                              "earliest_supported_for_assessment": earliest.isoformat()},
        "status": "blocked_by_source_window" if blocked else "eligible_recent_window",
        "observations": None,
        "missing_is_zero": False,
        "historical_archive_required": blocked,
        "scientific_score_modified": False,
    }
    result["report_payload_sha256"] = digest(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    wiki = sub.add_parser("wikimedia")
    wiki.add_argument("--topic-id", required=True)
    wiki.add_argument("--article-title", required=True)
    wiki.add_argument("--project", default="en.wikipedia.org")
    wiki.add_argument("--start", required=True)
    wiki.add_argument("--end", required=True)
    wiki.add_argument("--output", type=Path, required=True)
    gdelt = sub.add_parser("gdelt-assess")
    gdelt.add_argument("--start", required=True)
    gdelt.add_argument("--end", required=True)
    gdelt.add_argument("--as-of", required=True)
    gdelt.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("External observation is immutable; choose a new output.")
    if args.command == "wikimedia":
        result = fetch_wikimedia(
            WikimediaMapping(args.topic_id, args.article_title, args.project),
            args.start, args.end,
        )
    else:
        result = gdelt_doc_assessment(args.start, args.end, args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"output": str(args.output), "source": result["source"],
                      "status": result["status"],
                      "sha256": result["report_payload_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
