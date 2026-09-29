"""Semantic Scholar search as overlapping bibliographic enrichment only."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


FIELDS = ("paperId,externalIds,title,year,publicationDate,authors,"
          "citationCount,referenceCount,fieldsOfStudy,url")


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


@dataclass(frozen=True)
class ScholarQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 25

    def validate(self) -> tuple[date, date, date]:
        text = self.query.strip()
        if not self.topic_id.strip() or not 2 <= len(text) <= 200:
            raise ValueError("Нужны topic ID и научный запрос от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in text):
            raise ValueError("Научный запрос содержит управляющие символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный интервал Semantic Scholar.")
        if not 1 <= self.max_records <= int(load_policy()["scholar"]["max_records"]):
            raise ValueError("Превышен лимит Semantic Scholar.")
        return start, end, as_of


def request_url(query: ScholarQuery) -> str:
    start, end, _ = query.validate()
    params = {"query": query.query.strip(), "limit": query.max_records,
              "offset": 0, "fields": FIELDS, "year": f"{start.year}-{end.year}"}
    return f"{load_policy()['scholar']['search_url']}?{urlencode(params)}"


def parse_response(query: ScholarQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, final_url: str | None = None, http_status: int = 200,
                   authenticated: bool = False) -> dict:
    start, end, as_of = query.validate()
    maximum = int(load_policy()["scholar"]["max_response_bytes"])
    if len(payload_bytes) > maximum:
        raise ValueError("Semantic Scholar response exceeds the bounded connector limit.")
    payload = json.loads(payload_bytes)
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("Semantic Scholar response has no data list.")
    observations, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid Semantic Scholar paper record.")
        paper_id, title = str(row.get("paperId") or "").strip(), str(row.get("title") or "").strip()
        if not paper_id or not title:
            raise ValueError("Semantic Scholar paper lacks ID or title.")
        published = row.get("publicationDate")
        try:
            published_day = date.fromisoformat(str(published)).isoformat() if published else None
        except ValueError:
            published_day = None
        year = row.get("year") if isinstance(row.get("year"), int) else None
        in_range = (published_day is not None and start.isoformat() <= published_day <= end.isoformat())
        if published_day is None:
            in_range = year is not None and start.year <= year <= end.year
        if not in_range or paper_id in seen:
            continue
        seen.add(paper_id)
        authors = row.get("authors") if isinstance(row.get("authors"), list) else []
        observations.append({
            "paper_id": paper_id, "external_ids": row.get("externalIds") or {},
            "title": title, "year": year, "publication_date": published_day,
            "authors": [author.get("name") for author in authors
                        if isinstance(author, dict) and author.get("name")],
            "citation_count_current": row.get("citationCount"),
            "reference_count_current": row.get("referenceCount"),
            "fields_of_study": row.get("fieldsOfStudy") or [],
            "url": row.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}",
        })
    observations.sort(key=lambda item: (item["publication_date"] or f"{item['year'] or 0:04d}-00-00",
                                        item["paper_id"]), reverse=True)
    total = payload.get("total") if isinstance(payload.get("total"), int) else None
    result = {
        "version": VERSION, "source": "semantic_scholar",
        "role": "bibliographic_enrichment_only", "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records, "year_filter": f"{start.year}-{end.year}"},
        "request": {"url": url, "final_url": final_url, "http_status": http_status,
                    "authenticated": authenticated, "credentials_stored": False},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations, "observed_paper_count": len(observations),
        "reported_total_results": total,
        "result_cap_reached": total is not None and total > len(rows),
        "source_result_is_exhaustive": total is not None and total == len(rows),
        "overlap_with_primary_corpus_unknown": True,
        "citation_counts_as_of_retrieval_only": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["scholar"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: ScholarQuery, status: str, retrieved_at: str, url: str,
                authenticated: bool, http_status: int | None = None,
                payload_bytes: bytes = b"", retry_after: str | None = None,
                reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "semantic_scholar",
        "role": "bibliographic_enrichment_only", "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records, "year_filter": f"{start.year}-{end.year}"},
        "request": {"url": url, "final_url": None, "http_status": http_status,
                    "retry_after": retry_after, "authenticated": authenticated,
                    "credentials_stored": False},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason, "observations": None,
        "observed_paper_count": None, "reported_total_results": None,
        "result_cap_reached": None, "source_result_is_exhaustive": False,
        "overlap_with_primary_corpus_unknown": True,
        "citation_counts_as_of_retrieval_only": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["scholar"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: ScholarQuery, timeout: float = 30.0, api_key: str | None = None) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    policy = load_policy()["scholar"]
    key = api_key if api_key is not None else os.environ.get(policy["credentials"]["key_env"])
    headers = {"Accept": "application/json", "User-Agent": "SAIA-research-prototype/0.4.30"}
    if key:
        headers[policy["credentials"]["header"]] = key
    maximum = int(policy["max_response_bytes"])
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as response:
            payload = response.read(maximum + 1)
            return parse_response(query, payload, retrieved, url, response.geturl(),
                                  response.status, bool(key))
    except HTTPError as error:
        payload = error.read(maximum + 1)
        status = "rate_limited" if error.code == 429 else (
            "credentials_rejected" if error.code in (401, 403) and bool(key)
            else "source_http_error")
        return unavailable(query, status, retrieved, url, bool(key), error.code, payload,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url, bool(key),
                           reason=type(error).__name__)
