"""Bounded UKRI Gateway to Research project evidence, separate from score."""
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


def _epoch_day(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


@dataclass(frozen=True)
class UKRIQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 25

    def validate(self) -> tuple[date, date, date]:
        text = self.query.strip()
        if not self.topic_id.strip() or not 2 <= len(text) <= 200:
            raise ValueError("Нужны topic ID и запрос UKRI от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in text):
            raise ValueError("Запрос UKRI содержит управляющие символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный интервал UKRI.")
        if not 1 <= self.max_records <= int(load_policy()["uk_funding"]["max_records"]):
            raise ValueError("Превышен лимит проектов UKRI.")
        return start, end, as_of


def request_url(query: UKRIQuery) -> str:
    query.validate()
    policy = load_policy()["uk_funding"]
    params = urlencode({"term": query.query.strip(), "page": 1,
                        "fetchSize": policy["source_page_size"]})
    return f"{policy['search_url']}?{params}"


def parse_response(query: UKRIQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    policy = load_policy()["uk_funding"]
    if len(payload_bytes) > int(policy["max_response_bytes"]):
        raise ValueError("UKRI response exceeds the bounded connector limit.")
    payload = json.loads(payload_bytes)
    result_set = payload.get("facetedSearchResultBean")
    if not isinstance(result_set, dict) or not isinstance(result_set.get("results"), list):
        raise ValueError("UKRI response has no project results list.")
    observations, seen = [], set()
    for row in result_set["results"]:
        composition = row.get("projectComposition") if isinstance(row, dict) else None
        project = composition.get("project") if isinstance(composition, dict) else None
        if not isinstance(project, dict):
            continue
        grant_reference = str(project.get("grantReference") or project.get("id") or "").strip()
        title = str(project.get("title") or "").strip()
        fund = project.get("fund") if isinstance(project.get("fund"), dict) else {}
        project_start = _epoch_day(fund.get("start"))
        if not grant_reference or not title or not project_start:
            continue
        if not start.isoformat() <= project_start <= end.isoformat() or grant_reference in seen:
            continue
        seen.add(grant_reference)
        lead = composition.get("leadResearchOrganisation") or {}
        funder = fund.get("funder") if isinstance(fund.get("funder"), dict) else {}
        principal = composition.get("principalInvestigators") or []
        haystack = f"{title} {project.get('abstractText') or ''}".casefold()
        terms = [part.casefold() for part in query.query.split() if part.strip()]
        lexical_all_terms_match = bool(terms) and all(term in haystack for term in terms)
        if not lexical_all_terms_match:
            continue
        observations.append({
            "project_id": project.get("id"),
            "grant_reference": grant_reference,
            "title": title,
            "abstract": project.get("abstractText"),
            "technical_summary": project.get("technicalSummary"),
            "project_start_date": project_start,
            "project_end_date": _epoch_day(fund.get("end")),
            "award_amount_gbp": fund.get("valuePounds"),
            "grant_category": project.get("grantCategory"),
            "lead_organisation": lead.get("name") if isinstance(lead, dict) else None,
            "funder": funder.get("name"),
            "principal_investigators": [
                person.get("fullName") for person in principal
                if isinstance(person, dict) and person.get("fullName")
            ],
            "lexical_all_terms_match": lexical_all_terms_match,
            "url": f"https://gtr.ukri.org/projects?ref={grant_reference}",
        })
        if len(observations) >= query.max_records:
            break
    observations.sort(key=lambda item: (item["project_start_date"], item["grant_reference"]), reverse=True)
    total = result_set.get("totalResults") if isinstance(result_set.get("totalResults"), int) else None
    header = payload.get("headerData") if isinstance(payload.get("headerData"), dict) else {}
    result = {
        "version": VERSION, "source": "ukri_gtr", "role": "research_funding_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status},
        "retrieved_at": retrieved_at,
        "source_last_refresh_date": header.get("lastRefreshDate"),
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations,
        "observed_project_count": len(observations),
        "reported_total_results": total,
        "observed_organisation_count": len({item["lead_organisation"] for item in observations if item["lead_organisation"]}),
        "observed_funder_count": len({item["funder"] for item in observations if item["funder"]}),
        "result_cap_reached": total is not None and total > len(result_set["results"]),
        "source_result_is_exhaustive": False,
        "strict_all_query_terms_filter_applied": True,
        "search_relevance_requires_review": True,
        "retrospective_safe": False,
        "missing_is_zero": False, "scientific_score_modified": False,
        "limitations": [policy["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: UKRIQuery, status: str, retrieved_at: str, url: str,
                http_status: int | None = None, payload_bytes: bytes = b"",
                retry_after: str | None = None, reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "ukri_gtr", "role": "research_funding_only",
        "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status, "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest() if payload_bytes else None,
        "status": status, "status_reason": reason, "observations": None,
        "observed_project_count": None, "reported_total_results": None,
        "source_result_is_exhaustive": False, "strict_all_query_terms_filter_applied": True,
        "search_relevance_requires_review": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["uk_funding"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: UKRIQuery, timeout: float = 30.0) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    maximum = int(load_policy()["uk_funding"]["max_response_bytes"])
    request = Request(url, headers={"Accept": "application/json",
                                    "User-Agent": "SAIA-research-prototype/0.4.31"})
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
