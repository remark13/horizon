"""Bounded NIH RePORTER project evidence, separate from scientific score."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from saia.external_sources import VERSION, load_policy
from saia.hybrid import digest


def _day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _iso_day(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class FundingQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 25

    def validate(self) -> tuple[date, date, date]:
        text = self.query.strip()
        if not self.topic_id.strip() or not 2 <= len(text) <= 200:
            raise ValueError("Нужны topic ID и запрос финансирования от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in text):
            raise ValueError("Запрос финансирования содержит управляющие символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный интервал финансирования.")
        if not 1 <= self.max_records <= int(load_policy()["funding"]["max_records"]):
            raise ValueError("Превышен лимит проектов финансирования.")
        return start, end, as_of


def request_body(query: FundingQuery) -> dict:
    start, end, _ = query.validate()
    return {
        "criteria": {
            "advanced_text_search": {
                "operator": "and", "search_field": "all",
                "search_text": query.query.strip(),
            },
            "project_start_date": {
                "from_date": start.isoformat(), "to_date": end.isoformat(),
            },
        },
        "include_fields": [
            "ApplId", "ProjectNum", "CoreProjectNum", "ProjectTitle", "AbstractText",
            "ProjectStartDate", "ProjectEndDate", "AwardNoticeDate",
            "Organization", "AwardAmount", "AgencyIcAdmin", "ProjectDetailUrl",
            "PrincipalInvestigators",
        ],
        "offset": 0, "limit": query.max_records,
        "sort_field": "project_start_date", "sort_order": "desc",
    }


def parse_response(query: FundingQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    maximum = int(load_policy()["funding"]["max_response_bytes"])
    if len(payload_bytes) > maximum:
        raise ValueError("NIH RePORTER response exceeds the bounded connector limit.")
    payload = json.loads(payload_bytes)
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise ValueError("NIH RePORTER response has no results list.")
    observations, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid NIH RePORTER project record.")
        project_num = str(row.get("project_num") or "").strip()
        title = str(row.get("project_title") or "").strip()
        project_start = _iso_day(row.get("project_start_date"))
        if not project_num or not title or not project_start:
            raise ValueError("NIH project lacks number, title or start date.")
        if not start.isoformat() <= project_start <= end.isoformat():
            raise ValueError("NIH project is outside the requested interval.")
        if project_num in seen:
            continue
        seen.add(project_num)
        agency = row.get("agency_ic_admin") or {}
        organization = row.get("organization") or {}
        investigators = row.get("principal_investigators") or []
        application_id = row.get("appl_id")
        observations.append({
            "project_num": project_num,
            "application_id": application_id,
            "core_project_num": row.get("core_project_num"),
            "project_title": title,
            "abstract": row.get("abstract_text"),
            "project_start_date": project_start,
            "project_end_date": _iso_day(row.get("project_end_date")),
            "award_notice_date": _iso_day(row.get("award_notice_date")),
            "award_amount_usd": row.get("award_amount"),
            "organization": organization.get("org_name"),
            "organization_country": organization.get("org_country"),
            "agency": agency.get("abbreviation") or agency.get("name"),
            "principal_investigators": [
                item.get("full_name") for item in investigators
                if isinstance(item, dict) and item.get("full_name")
            ],
            "url": row.get("project_detail_url") or (
                f"https://reporter.nih.gov/project-details/{application_id}"
                if application_id is not None else None
            ),
        })
    observations.sort(key=lambda item: (item["project_start_date"], item["project_num"]), reverse=True)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    total = meta.get("total") if isinstance(meta.get("total"), int) else None
    award_sum = sum(item["award_amount_usd"] for item in observations
                    if isinstance(item["award_amount_usd"], (int, float)))
    core_ids = {item["core_project_num"] for item in observations if item["core_project_num"]}
    result = {
        "version": VERSION, "source": "nih_reporter", "role": "research_funding_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records, "body": request_body(query)},
        "request": {"url": url, "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations,
        "observed_project_record_count": len(observations),
        "observed_core_project_count": len(core_ids) if core_ids else None,
        "observed_award_amount_sum_usd": award_sum,
        "reported_total_results": total,
        "project_family_deduplication_applied": False,
        "result_cap_reached": total is not None and total > len(observations),
        "source_result_is_exhaustive": total is not None and total == len(observations),
        "missing_is_zero": False, "stores_full_text": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["funding"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: FundingQuery, status: str, retrieved_at: str, url: str,
                http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "nih_reporter", "role": "research_funding_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records, "body": request_body(query)},
        "request": {"url": url, "http_status": http_status, "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason, "observations": None,
        "observed_project_record_count": None, "observed_core_project_count": None,
        "observed_award_amount_sum_usd": None, "reported_total_results": None,
        "project_family_deduplication_applied": False,
        "result_cap_reached": None, "source_result_is_exhaustive": False,
        "missing_is_zero": False, "stores_full_text": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["funding"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: FundingQuery, timeout: float = 30.0) -> dict:
    query.validate()
    policy = load_policy()["funding"]
    url, retrieved = policy["search_url"], datetime.now(timezone.utc).isoformat()
    body = json.dumps(request_body(query), separators=(",", ":")).encode()
    request = Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "SAIA-research-prototype/0.4.30",
    })
    maximum = int(policy["max_response_bytes"])
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
