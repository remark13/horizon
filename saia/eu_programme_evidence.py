"""Bounded EU Funding & Tenders topic evidence, separate from score."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _first(value: object) -> object | None:
    return value[0] if isinstance(value, list) and value else value


def _iso_day(value: object) -> str | None:
    text = str(_first(value) or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class EUProgrammeQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 10

    def validate(self) -> tuple[date, date, date]:
        text = self.query.strip()
        if not self.topic_id.strip() or not 2 <= len(text) <= 200:
            raise ValueError("Нужны topic ID и запрос программ ЕС от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in text):
            raise ValueError("Запрос программ ЕС содержит управляющие символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный интервал программ ЕС.")
        if not 1 <= self.max_records <= int(load_policy()["eu_programmes"]["max_records"]):
            raise ValueError("Превышен лимит программ ЕС.")
        return start, end, as_of


def search_filter() -> dict:
    return {"bool": {"must": [
        {"terms": {"type": ["1"]}},
        {"terms": {"language": ["en"]}},
    ]}}


def request_url(query: EUProgrammeQuery) -> str:
    query.validate()
    policy = load_policy()["eu_programmes"]
    params = urlencode({"apiKey": policy["public_api_key"], "text": query.query.strip(),
                        "pageSize": query.max_records, "pageNumber": 1})
    return f"{policy['search_url']}?{params}"


def multipart_body() -> tuple[bytes, str]:
    boundary = "----SAIAExternalSource0431"
    query_json = json.dumps(search_filter(), separators=(",", ":")).encode()
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="query"; filename="query.json"\r\n'
        "Content-Type: application/json\r\n\r\n"
    ).encode() + query_json + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def parse_response(query: EUProgrammeQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    policy = load_policy()["eu_programmes"]
    if len(payload_bytes) > int(policy["max_response_bytes"]):
        raise ValueError("EU Funding & Tenders response exceeds the bounded limit.")
    payload = json.loads(payload_bytes)
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("EU Funding & Tenders response has no results list.")
    observations, seen = [], set()
    for row in rows:
        metadata = row.get("metadata") if isinstance(row, dict) else None
        if not isinstance(metadata, dict):
            continue
        identifier = str(_first(metadata.get("identifier")) or "").strip()
        item_url = str(row.get("url") or "")
        opened = _iso_day(metadata.get("startDate"))
        if not identifier or "/topicDetails/" not in item_url or not opened:
            continue
        if not start.isoformat() <= opened <= end.isoformat() or identifier in seen:
            continue
        seen.add(identifier)
        keywords = metadata.get("keywords") if isinstance(metadata.get("keywords"), list) else []
        observations.append({
            "programme_topic_id": identifier,
            "title": str(row.get("summary") or identifier),
            "call_identifier": _first(metadata.get("callIdentifier")),
            "call_title": _first(metadata.get("callTitle")),
            "programme_period": _first(metadata.get("programmePeriod")),
            "opening_date": opened,
            "deadline_date": _iso_day(metadata.get("deadlineDate")),
            "status_code": _first(metadata.get("status")),
            "keywords": [str(value) for value in keywords[:30]],
            "url": item_url,
        })
        if len(observations) >= query.max_records:
            break
    observations.sort(key=lambda item: (item["opening_date"], item["programme_topic_id"]), reverse=True)
    total = payload.get("totalResults") if isinstance(payload.get("totalResults"), int) else None
    result = {
        "version": VERSION, "source": "eu_funding_tenders", "role": "research_programme_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records, "search_filter": search_filter()},
        "request": {"url": url, "http_status": http_status, "api_version": payload.get("apiVersion")},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations,
        "observed_topic_count": len(observations),
        "reported_total_mixed_results": total,
        "result_cap_reached": total is not None and total > len(rows),
        "source_result_is_exhaustive": False,
        "mixed_index_filtered_to_topic_cards": True,
        "retrospective_safe": False,
        "missing_is_zero": False, "scientific_score_modified": False,
        "limitations": [policy["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: EUProgrammeQuery, status: str, retrieved_at: str, url: str,
                http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "eu_funding_tenders", "role": "research_programme_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records, "search_filter": search_filter()},
        "request": {"url": url, "http_status": http_status, "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason, "observations": None,
        "observed_topic_count": None, "reported_total_mixed_results": None,
        "source_result_is_exhaustive": False, "mixed_index_filtered_to_topic_cards": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["eu_programmes"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: EUProgrammeQuery, timeout: float = 40.0) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    policy = load_policy()["eu_programmes"]
    maximum = int(policy["max_response_bytes"])
    body, content_type = multipart_body()
    request = Request(url, data=body, method="POST", headers={
        "Accept": "application/json", "Content-Type": content_type,
        "User-Agent": "SAIA-research-prototype/0.4.31",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(maximum + 1)
            return parse_response(query, payload, retrieved, url, response.status)
    except HTTPError as error:
        payload = error.read(maximum + 1)
        status = "rate_limited" if error.code == 429 else "source_http_error"
        return unavailable(query, status, retrieved, url, error.code, payload,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url,
                           reason=type(error).__name__)
