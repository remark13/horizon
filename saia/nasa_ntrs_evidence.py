"""Bounded NASA STI metadata probe; never a second scientific score."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _date_prefix(value: object) -> str | None:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class NasaNtrsQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 20

    def validate(self) -> tuple[date, date, date]:
        phrase = " ".join(self.query.split())
        if not self.topic_id.strip() or not 2 <= len(phrase) <= 200:
            raise ValueError("Нужны тема и поисковая фраза от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in self.query) or any(char in phrase for char in '"\\[]{}'):
            raise ValueError("Поисковая фраза содержит недопустимые служебные символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный период NASA NTRS.")
        if not 1 <= self.max_records <= int(load_policy()["nasa_ntrs"]["max_records"]):
            raise ValueError("Превышен лимит NASA NTRS.")
        return start, end, as_of


def request_url(query: NasaNtrsQuery) -> str:
    start, end, _ = query.validate()
    policy = load_policy()["nasa_ntrs"]
    args = urlencode({
        "q": " ".join(query.query.split()),
        "published.gte": start.isoformat(), "published.lte": end.isoformat(),
        "page.size": int(policy["source_page_size"]),
    })
    return f"{policy['search_url']}?{args}"


def _record_date(row: dict) -> tuple[str | None, str | None]:
    pubs = row.get("publications")
    dates = [_date_prefix(item.get("publicationDate")) for item in pubs if isinstance(item, dict)] if isinstance(pubs, list) else []
    dates = [item for item in dates if item]
    if dates:
        return min(dates), "publication"
    distributed = _date_prefix(row.get("distributionDate"))
    return distributed, "distribution" if distributed else None


def parse_response(query: NasaNtrsQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    policy = load_policy()["nasa_ntrs"]
    if len(payload_bytes) > int(policy["max_response_bytes"]):
        raise ValueError("Ответ NASA NTRS превысил лимит.")
    payload = json.loads(payload_bytes)
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("NASA NTRS не вернул список записей.")
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    phrase = " ".join(query.query.casefold().split())
    eligible, seen = [], set()
    excluded_nonpublication = excluded_unmatched = excluded_abstract_only = excluded_unknown_dates = examined = 0
    for row in rows:
        examined += 1
        if not isinstance(row, dict):
            continue
        identifier, title = row.get("id"), row.get("title")
        if not isinstance(identifier, int) or identifier <= 0 or not isinstance(title, str) or not title.strip():
            continue
        if "PATENT" in str(row.get("stiType") or "").upper():
            excluded_nonpublication += 1
            continue
        published, date_basis = _record_date(row)
        if not published:
            excluded_unknown_dates += 1
            continue
        if not start.isoformat() <= published <= end.isoformat():
            continue
        keywords = row.get("keywords") if isinstance(row.get("keywords"), list) else []
        visible = [title, row.get("abstract") or ""] + [str(item) for item in keywords]
        fields = [name for name, value in zip(
            ("title", "abstract", *["keyword"] * len(keywords)), visible
        ) if phrase in " ".join(str(value).casefold().split())]
        if not fields:
            excluded_unmatched += 1
            continue
        if "title" not in fields and "keyword" not in fields:
            excluded_abstract_only += 1
            continue
        if identifier in seen:
            continue
        seen.add(identifier)
        eligible.append({
            "citation_id": identifier, "title": title.strip(),
            "publication_date": published, "date_basis": date_basis,
            "sti_type": row.get("stiType"), "center": (row.get("center") or {}).get("name")
            if isinstance(row.get("center"), dict) else None,
            "query_match_fields": sorted(set(fields)),
            "url": f"https://ntrs.nasa.gov/citations/{identifier}",
        })
    eligible.sort(key=lambda item: (
        "title" not in item["query_match_fields"],
        "keyword" not in item["query_match_fields"],
        -int(item["publication_date"].replace("-", "")),
        item["citation_id"],
    ))
    observations = eligible[:query.max_records]
    total = stats.get("total") if isinstance(stats.get("total"), int) else None
    result = {
        "version": VERSION, "source": "nasa_ntrs", "role": "bibliographic_enrichment_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": " ".join(query.query.split()), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations, "observed_paper_count": len(observations),
        "reported_total_search_hits": total, "source_page_records": len(rows),
        "examined_source_records": examined, "eligible_source_records": len(eligible),
        "excluded_nonpublication_records": excluded_nonpublication,
        "excluded_without_visible_query_match": excluded_unmatched,
        "excluded_abstract_only_match": excluded_abstract_only,
        "excluded_unknown_dates": excluded_unknown_dates,
        "source_result_is_exhaustive": (
            total is not None and total <= len(rows) and len(eligible) <= query.max_records
        ),
        "overlap_with_openalex_requires_dedup": True,
        "independent_publication_count_established": False,
        "title_match_is_not_topic_validation": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False, "limitations": [policy["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: NasaNtrsQuery, status: str, retrieved_at: str, url: str,
                http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "nasa_ntrs", "role": "bibliographic_enrichment_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": " ".join(query.query.split()), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status, "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason, "observations": None,
        "observed_paper_count": None, "reported_total_search_hits": None,
        "source_page_records": None, "examined_source_records": None,
        "eligible_source_records": None,
        "excluded_nonpublication_records": None,
        "excluded_without_visible_query_match": None,
        "excluded_abstract_only_match": None, "excluded_unknown_dates": None,
        "source_result_is_exhaustive": False,
        "overlap_with_openalex_requires_dedup": True,
        "independent_publication_count_established": False,
        "title_match_is_not_topic_validation": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["nasa_ntrs"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: NasaNtrsQuery, timeout: float = 35.0) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    maximum = int(load_policy()["nasa_ntrs"]["max_response_bytes"])
    request = Request(url, headers={
        "Accept": "application/json", "User-Agent": "SAIA-research-prototype/0.4.35",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(maximum + 1)
            try:
                return parse_response(query, payload, retrieved, url, response.status)
            except (ValueError, json.JSONDecodeError) as error:
                return unavailable(query, "source_invalid_response", retrieved, url,
                                   response.status, payload, reason=type(error).__name__)
    except HTTPError as error:
        payload = error.read(maximum + 1)
        return unavailable(query, "rate_limited" if error.code == 429 else "source_http_error",
                           retrieved, url, error.code, payload,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url,
                           reason=type(error).__name__)
