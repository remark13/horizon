"""Bounded Europe PMC biomedical metadata, never independent scientific score."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


PUBLICATION_SOURCES = {"MED", "PMC", "PPR", "AGR"}


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _full_date(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class EuropePmcQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 20

    def validate(self) -> tuple[date, date, date]:
        text = " ".join(self.query.split())
        if not self.topic_id.strip() or not 2 <= len(text) <= 200:
            raise ValueError("Нужны тема и поисковая фраза от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in text) or any(char in text for char in '"\\[]{}'):
            raise ValueError("Поисковая фраза содержит недопустимые служебные символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный период Europe PMC.")
        if not 1 <= self.max_records <= int(load_policy()["europe_pmc"]["max_records"]):
            raise ValueError("Превышен лимит Europe PMC.")
        return start, end, as_of


def request_url(query: EuropePmcQuery) -> str:
    start, end, _ = query.validate()
    policy = load_policy()["europe_pmc"]
    phrase = " ".join(query.query.split())
    expression = (
        f'TITLE_ABS:"{phrase}" AND FIRST_PDATE:[{start} TO {end}] '
        'AND (SRC:MED OR SRC:PMC OR SRC:PPR OR SRC:AGR)'
    )
    params = urlencode({
        "query": expression, "format": "json", "resultType": "lite",
        "pageSize": int(policy["source_page_size"]),
    })
    return f"{policy['search_url']}?{params}"


def _identity_keys(row: dict) -> set[str]:
    keys = set()
    doi = row.get("doi")
    if isinstance(doi, str) and doi.strip():
        keys.add("doi:" + re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.I).casefold())
    for field in ("pmid", "pmcid"):
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            keys.add(field + ":" + value.strip().casefold())
    if not keys:
        keys.add("record:" + str(row.get("source")) + ":" + str(row.get("id")))
    return keys


def parse_response(query: EuropePmcQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    policy = load_policy()["europe_pmc"]
    if len(payload_bytes) > int(policy["max_response_bytes"]):
        raise ValueError("Ответ Europe PMC превысил лимит.")
    payload = json.loads(payload_bytes)
    listing = payload.get("resultList") if isinstance(payload, dict) else None
    rows = listing.get("result") if isinstance(listing, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Europe PMC не вернул список публикаций.")
    observations, seen_keys = [], set()
    unknown_dates = excluded_types = duplicate_records = examined = 0
    phrase = " ".join(query.query.casefold().split())
    for row in rows:
        examined += 1
        if not isinstance(row, dict):
            continue
        source, publication_id = row.get("source"), row.get("id")
        if source not in PUBLICATION_SOURCES:
            excluded_types += 1
            continue
        title = row.get("title")
        if not (isinstance(publication_id, str)
                and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", publication_id)
                and isinstance(title, str) and title.strip()):
            continue
        published = _full_date(row.get("firstPublicationDate"))
        if not published:
            unknown_dates += 1
            continue
        if not start.isoformat() <= published <= end.isoformat():
            continue
        keys = _identity_keys(row)
        if keys & seen_keys:
            duplicate_records += 1
            continue
        seen_keys.update(keys)
        doi = row.get("doi") if isinstance(row.get("doi"), str) else None
        observations.append({
            "publication_id": publication_id,
            "source_collection": source,
            "title": title.strip(),
            "publication_date": published,
            "doi": doi,
            "pmid": row.get("pmid"),
            "pmcid": row.get("pmcid"),
            "publication_type": row.get("pubType"),
            "is_preprint": source == "PPR",
            "title_contains_query_phrase": phrase in " ".join(title.casefold().split()),
            "url": "https://europepmc.org/article/" + quote(source, safe="") + "/" + quote(publication_id, safe=""),
        })
        if len(observations) >= query.max_records:
            break
    total = payload.get("hitCount") if isinstance(payload.get("hitCount"), int) else None
    result = {
        "version": VERSION, "source": "europe_pmc",
        "role": "bibliographic_enrichment_only", "topic_id": query.topic_id.strip(),
        "query": {"text": " ".join(query.query.split()), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations, "observed_paper_count": len(observations),
        "reported_total_search_hits": total,
        "source_page_records": len(rows), "examined_source_records": examined,
        "excluded_nonpublication_records": excluded_types,
        "excluded_unknown_dates": unknown_dates,
        "duplicate_source_records_collapsed": duplicate_records,
        "source_result_is_exhaustive": total is not None and total <= len(rows),
        "overlap_with_openalex_and_preprints_requires_dedup": True,
        "independent_publication_count_established": False,
        "title_match_is_not_topic_validation": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [policy["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: EuropePmcQuery, status: str, retrieved_at: str, url: str,
                http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "europe_pmc",
        "role": "bibliographic_enrichment_only", "topic_id": query.topic_id.strip(),
        "query": {"text": " ".join(query.query.split()), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status, "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest()
        if payload_bytes else None,
        "status": status, "status_reason": reason,
        "observations": None, "observed_paper_count": None,
        "reported_total_search_hits": None,
        "source_page_records": None, "examined_source_records": None,
        "excluded_nonpublication_records": None,
        "excluded_unknown_dates": None,
        "duplicate_source_records_collapsed": None,
        "source_result_is_exhaustive": False,
        "overlap_with_openalex_and_preprints_requires_dedup": True,
        "independent_publication_count_established": False,
        "title_match_is_not_topic_validation": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["europe_pmc"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: EuropePmcQuery, timeout: float = 35.0) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    maximum = int(load_policy()["europe_pmc"]["max_response_bytes"])
    request = Request(url, headers={
        "Accept": "application/json", "User-Agent": "SAIA-research-prototype/0.4.33",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(maximum + 1)
            try:
                return parse_response(query, payload, retrieved, url, response.status)
            except ValueError as error:
                return unavailable(query, "source_invalid_response", retrieved, url,
                                   response.status, payload, reason=type(error).__name__)
    except HTTPError as error:
        payload = error.read(maximum + 1)
        status = "rate_limited" if error.code == 429 else "source_http_error"
        return unavailable(query, status, retrieved, url, error.code, payload,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url,
                           reason=type(error).__name__)
