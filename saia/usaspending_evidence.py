"""Bounded USAspending contract search; procurement is not a scientific score."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _normal(text: str) -> str:
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class USAspendingQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 10

    def validate(self) -> tuple[date, date, date]:
        phrase = " ".join(self.query.split())
        if not 1 <= len(self.topic_id.strip()) <= 200 or not 2 <= len(phrase) <= 200:
            raise ValueError("Нужны тема и поисковая фраза от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in self.query):
            raise ValueError("Поисковая фраза содержит служебные символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        earliest = date.fromisoformat(load_policy()["usaspending"]["earliest_search_date"])
        if start < earliest or end < start or end > as_of:
            raise ValueError("Некорректный период USAspending; доступный поиск начинается с 01.10.2007.")
        if not 1 <= self.max_records <= int(load_policy()["usaspending"]["max_records"]):
            raise ValueError("Превышен лимит USAspending.")
        return start, end, as_of


def request_payload(query: USAspendingQuery) -> dict:
    start, end, _ = query.validate()
    return {
        "filters": {
            "keywords": [" ".join(query.query.split())],
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
        },
        "fields": ["Award ID", "Recipient Name", "Description", "Start Date",
                   "Award Amount", "Awarding Agency"],
        "page": 1, "limit": int(load_policy()["usaspending"]["source_page_size"]),
        "sort": "Start Date", "order": "desc", "spending_level": "awards",
    }


def _request_bytes(query: USAspendingQuery) -> bytes:
    return json.dumps(request_payload(query), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _identity(query: USAspendingQuery, url: str, retrieved_at: str,
              http_status: int | None, raw: bytes) -> dict:
    start, end, as_of = query.validate()
    request_bytes = _request_bytes(query)
    return {
        "version": VERSION, "source": "usaspending", "role": "public_procurement_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": " ".join(query.query.split()), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records, "award_kind": "US federal contracts"},
        "request": {"url": url, "method": "POST", "payload": request_payload(query),
                    "payload_sha256": hashlib.sha256(request_bytes).hexdigest(),
                    "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(raw).hexdigest() if raw else None,
        "scientific_score_modified": False, "missing_is_zero": False,
        "retrospective_safe": False, "private_investment_established": False,
        "market_size_established": False,
        "limitations": [load_policy()["usaspending"]["limitation"]],
    }


def parse_response(query: USAspendingQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, _ = query.validate()
    policy = load_policy()["usaspending"]
    if len(payload_bytes) > int(policy["max_response_bytes"]):
        raise ValueError("Ответ USAspending превысил лимит.")
    payload = json.loads(payload_bytes)
    rows = payload.get("results") if isinstance(payload, dict) else None
    metadata = payload.get("page_metadata") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not isinstance(metadata, dict) or not isinstance(metadata.get("hasNext"), bool):
        raise ValueError("USAspending не вернул ожидаемую страницу контрактов.")
    if len(rows) > int(policy["source_page_size"]):
        raise ValueError("USAspending вернул больше записей, чем запрошено.")
    phrase = _normal(query.query)
    eligible, seen = [], set()
    excluded_unmatched = excluded_bad_date = excluded_bad_identity = 0
    for row in rows:
        if not isinstance(row, dict):
            excluded_bad_identity += 1
            continue
        description = row.get("Description")
        generated_id = row.get("generated_internal_id")
        award_id = row.get("Award ID")
        if not isinstance(description, str) or not description.strip() or not isinstance(generated_id, str) or not re.fullmatch(r"CONT_AWD_[A-Za-z0-9_-]{1,240}", generated_id) or not isinstance(award_id, str) or not award_id.strip():
            excluded_bad_identity += 1
            continue
        if phrase not in _normal(description):
            excluded_unmatched += 1
            continue
        award_date = row.get("Start Date")
        try:
            observed_date = date.fromisoformat(award_date) if isinstance(award_date, str) else None
        except ValueError:
            observed_date = None
        if observed_date is None or not start <= observed_date <= end:
            excluded_bad_date += 1
            continue
        if generated_id in seen:
            continue
        seen.add(generated_id)
        amount = row.get("Award Amount")
        eligible.append({
            "generated_internal_id": generated_id,
            "award_id": award_id.strip(),
            "title": description.strip()[:160],
            "recipient": row.get("Recipient Name") if isinstance(row.get("Recipient Name"), str) else None,
            "agency": row.get("Awarding Agency") if isinstance(row.get("Awarding Agency"), str) else None,
            "award_start_date": observed_date.isoformat(),
            "award_amount_usd": amount if isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount) else None,
            "query_match_fields": ["description"],
            "url": "https://www.usaspending.gov/award/" + quote(generated_id, safe="_-"),
        })
    observations = eligible[:query.max_records]
    report = _identity(query, url, retrieved_at, http_status, payload_bytes)
    report.update({
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations, "observed_award_count": len(observations),
        "source_page_records": len(rows), "source_page_has_next": metadata["hasNext"],
        "eligible_source_records": len(eligible),
        "excluded_without_visible_query_match": excluded_unmatched,
        "excluded_without_valid_award_date": excluded_bad_date,
        "excluded_without_valid_identity": excluded_bad_identity,
        "source_result_is_exhaustive": not metadata["hasNext"] and len(eligible) <= query.max_records,
        "reported_total_search_hits": None,
        "visible_match_is_not_topic_validation": True,
    })
    report["report_payload_sha256"] = digest(report)
    return report


def unavailable(query: USAspendingQuery, status: str, retrieved_at: str,
                url: str, http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None) -> dict:
    report = _identity(query, url, retrieved_at, http_status, payload_bytes)
    report["request"]["retry_after"] = retry_after
    report.update({
        "status": status, "status_reason": reason, "observations": None,
        "observed_award_count": None, "source_page_records": None,
        "source_page_has_next": None, "eligible_source_records": None,
        "excluded_without_visible_query_match": None,
        "excluded_without_valid_award_date": None,
        "excluded_without_valid_identity": None,
        "source_result_is_exhaustive": False,
        "reported_total_search_hits": None,
        "visible_match_is_not_topic_validation": True,
    })
    report["report_payload_sha256"] = digest(report)
    return report


def fetch(query: USAspendingQuery, timeout: float = 25.0) -> dict:
    policy = load_policy()["usaspending"]
    url, retrieved = policy["search_url"], datetime.now(timezone.utc).isoformat()
    maximum = int(policy["max_response_bytes"])
    request = Request(url, data=_request_bytes(query), method="POST", headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "SAIA-research-prototype/0.4.36",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(maximum + 1)
            try:
                return parse_response(query, raw, retrieved, url, response.status)
            except (ValueError, json.JSONDecodeError) as error:
                return unavailable(query, "source_invalid_response", retrieved, url,
                                   response.status, raw, reason=type(error).__name__)
    except HTTPError as error:
        raw = error.read(maximum + 1)
        return unavailable(query, "rate_limited" if error.code == 429 else "source_http_error",
                           retrieved, url, error.code, raw,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url,
                           reason=type(error).__name__)
