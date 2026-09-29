"""Bounded ClinicalTrials.gov v2 observations, separate from scientific score."""
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


@dataclass(frozen=True)
class ClinicalTrialQuery:
    topic_id: str
    query: str
    start: str | date
    end: str | date
    as_of: str | date
    max_records: int = 20

    def validate(self) -> tuple[date, date, date]:
        text = self.query.strip()
        if not self.topic_id.strip() or not 2 <= len(text) <= 200:
            raise ValueError("Нужны тема и поисковая фраза от 2 до 200 знаков.")
        if any(ord(char) < 32 for char in text):
            raise ValueError("Поисковая фраза содержит управляющие символы.")
        start, end, as_of = _day(self.start), _day(self.end), _day(self.as_of)
        if end < start or end > as_of:
            raise ValueError("Некорректный период клинических исследований.")
        if not 1 <= self.max_records <= int(load_policy()["clinical_trials"]["max_records"]):
            raise ValueError("Превышен лимит клинических исследований.")
        return start, end, as_of


def request_url(query: ClinicalTrialQuery) -> str:
    query.validate()
    policy = load_policy()["clinical_trials"]
    params = urlencode({
        "query.term": query.query.strip(),
        "pageSize": int(policy["source_page_size"]),
        "format": "json",
        "countTotal": "true",
    })
    return f"{policy['search_url']}?{params}"


def _full_date(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _match_fields(query_text: str, *, title: str, description: dict,
                  interventions: list, keywords: list) -> list[str]:
    """Keep only visibly attributable matches from the broad registry search."""
    needle = " ".join(query_text.casefold().split())

    def contains(value: object) -> bool:
        return isinstance(value, str) and needle in " ".join(value.casefold().split())

    description = description if isinstance(description, dict) else {}
    interventions = interventions if isinstance(interventions, list) else []
    keywords = keywords if isinstance(keywords, list) else []
    matched = []
    if contains(title):
        matched.append("title")
    if any(contains(value.get("name")) for value in interventions if isinstance(value, dict)):
        matched.append("intervention")
    if any(contains(value) for value in keywords):
        matched.append("keyword")
    if contains(description.get("briefSummary")):
        matched.append("brief_summary")
    return matched


def parse_response(query: ClinicalTrialQuery, payload_bytes: bytes,
                   retrieved_at: str, url: str, http_status: int = 200) -> dict:
    start, end, as_of = query.validate()
    policy = load_policy()["clinical_trials"]
    if len(payload_bytes) > int(policy["max_response_bytes"]):
        raise ValueError("Ответ реестра исследований превысил лимит.")
    payload = json.loads(payload_bytes)
    studies = payload.get("studies") if isinstance(payload, dict) else None
    if not isinstance(studies, list):
        raise ValueError("В ответе ClinicalTrials.gov нет списка исследований.")
    observations, seen = [], set()
    unknown_dates = 0
    unmatched_visible_text = 0
    examined_records = 0
    for item in studies:
        examined_records += 1
        protocol = item.get("protocolSection") if isinstance(item, dict) else None
        if not isinstance(protocol, dict):
            continue
        identity = protocol.get("identificationModule") or {}
        status = protocol.get("statusModule") or {}
        design = protocol.get("designModule") or {}
        sponsors = protocol.get("sponsorCollaboratorsModule") or {}
        conditions = protocol.get("conditionsModule") or {}
        arms = protocol.get("armsInterventionsModule") or {}
        description = protocol.get("descriptionModule") or {}
        nct_id = identity.get("nctId")
        title = identity.get("briefTitle")
        first_posted = _full_date((status.get("studyFirstPostDateStruct") or {}).get("date"))
        if not first_posted:
            unknown_dates += 1
            continue
        if not (isinstance(nct_id, str) and re.fullmatch(r"NCT\d{8}", nct_id)
                and isinstance(title, str) and title.strip()):
            continue
        if nct_id in seen or not start.isoformat() <= first_posted <= end.isoformat():
            continue
        lead = sponsors.get("leadSponsor") or {}
        interventions = arms.get("interventions") or []
        match_fields = _match_fields(
            query.query.strip(), title=title, description=description,
            interventions=interventions, keywords=conditions.get("keywords") or [],
        )
        if not match_fields:
            unmatched_visible_text += 1
            continue
        seen.add(nct_id)
        observations.append({
            "nct_id": nct_id,
            "title": title.strip(),
            "first_posted_date": first_posted,
            "last_update_posted_date": _full_date(
                (status.get("lastUpdatePostDateStruct") or {}).get("date")),
            "overall_status": status.get("overallStatus"),
            "study_type": design.get("studyType"),
            "lead_sponsor": lead.get("name") if isinstance(lead, dict) else None,
            "conditions": [value for value in conditions.get("conditions", [])
                           if isinstance(value, str)][:10],
            "interventions": [value.get("name") for value in interventions
                              if isinstance(value, dict) and value.get("name")][:10],
            "has_results": bool(item.get("hasResults")),
            "query_match_fields": match_fields,
            "topic_relevance_expert_verified": False,
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
        })
        if len(observations) >= query.max_records:
            break
    total = payload.get("totalCount") if isinstance(payload.get("totalCount"), int) else None
    result = {
        "version": VERSION, "source": "clinicaltrials_gov",
        "role": "clinical_translation_only", "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations,
        "observed_study_count": len(observations),
        "reported_total_search_hits": total,
        "source_page_records": len(studies),
        "examined_source_records": examined_records,
        "unknown_first_posted_dates": unknown_dates,
        "excluded_without_visible_query_match": unmatched_visible_text,
        "local_match_fields": ["title", "intervention", "keyword", "brief_summary"],
        "local_match_is_exhaustive": False,
        "source_result_is_exhaustive": False,
        "search_relevance_requires_review": True,
        "retrospective_safe": False,
        "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [policy["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def unavailable(query: ClinicalTrialQuery, status: str, retrieved_at: str,
                url: str, http_status: int | None = None,
                payload_bytes: bytes = b"", retry_after: str | None = None,
                reason: str | None = None) -> dict:
    start, end, as_of = query.validate()
    result = {
        "version": VERSION, "source": "clinicaltrials_gov",
        "role": "clinical_translation_only", "topic_id": query.topic_id.strip(),
        "query": {"text": query.query.strip(), "start": start.isoformat(),
                  "end": end.isoformat(), "as_of": as_of.isoformat(),
                  "max_records": query.max_records},
        "request": {"url": url, "http_status": http_status,
                    "retry_after": retry_after},
        "retrieved_at": retrieved_at,
        "raw_response_bytes_sha256": hashlib.sha256(payload_bytes).hexdigest()
        if payload_bytes else None,
        "status": status, "status_reason": reason,
        "observations": None, "observed_study_count": None,
        "reported_total_search_hits": None,
        "source_page_records": None,
        "examined_source_records": None,
        "excluded_without_visible_query_match": None,
        "local_match_fields": ["title", "intervention", "keyword", "brief_summary"],
        "local_match_is_exhaustive": False,
        "source_result_is_exhaustive": False,
        "search_relevance_requires_review": True,
        "retrospective_safe": False, "missing_is_zero": False,
        "scientific_score_modified": False,
        "limitations": [load_policy()["clinical_trials"]["limitation"]],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def fetch(query: ClinicalTrialQuery, timeout: float = 30.0) -> dict:
    url, retrieved = request_url(query), datetime.now(timezone.utc).isoformat()
    maximum = int(load_policy()["clinical_trials"]["max_response_bytes"])
    request = Request(url, headers={
        "Accept": "application/json", "User-Agent": "SAIA-research-prototype/0.4.32",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(maximum + 1)
            return parse_response(query, payload, retrieved, url, response.status)
    except HTTPError as error:
        payload = error.read(maximum + 1)
        code = "rate_limited" if error.code == 429 else "source_http_error"
        return unavailable(query, code, retrieved, url, error.code, payload,
                           error.headers.get("Retry-After"), f"http_{error.code}")
    except (URLError, TimeoutError) as error:
        return unavailable(query, "source_unavailable", retrieved, url,
                           reason=type(error).__name__)
