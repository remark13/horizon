"""NSF grant awards, not venture investments or successful-research verdicts."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlencode

from saia.external_sources import load_policy
from saia.public_metadata import MetadataQuery, all_terms_match, amount, base_report, fetch_bounded, finish, json_payload, terms


class NSFQuery(MetadataQuery):
    section = "nsf_funding"


def request_url(query: NSFQuery) -> str:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    # Live probes: a plain phrase is OR-like; individually quoted AND terms
    # returned no matches. Bare generated AND terms work, with a local recheck.
    keyword = " AND ".join('"' + word + '"' if word.upper() in {"AND", "OR", "NOT"} else word
                             for word in terms(query.query))
    return policy["search_url"] + "?" + urlencode({
        "keyword": keyword, "rpp": policy["source_page_size"], "offset": 0,
        "dateStart": start.strftime("%m/%d/%Y"), "dateEnd": end.strftime("%m/%d/%Y"),
    })


def _day(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
        return None
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _notifications_have_error(value: object) -> bool:
    if isinstance(value, list):
        return any(_notifications_have_error(item) for item in value)
    if isinstance(value, dict):
        return any(_notifications_have_error(item) for item in value.values())
    return isinstance(value, str) and bool(re.search(r"\b(?:ERROR|FATAL)\b", value, re.IGNORECASE))


def parse_response(query: NSFQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    payload = json_payload(payload_bytes, query.section)
    response = payload.get("response")
    if not isinstance(response, dict) or _notifications_have_error(payload.get("serviceNotification")) or _notifications_have_error(response.get("serviceNotification")):
        raise ValueError("NSF returned an error or an invalid response envelope.")
    metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
    total = metadata.get("totalCount")
    total = total if type(total) is int and total >= 0 else None
    rows = response.get("award", [] if total == 0 else None)
    if not isinstance(rows, list) or len(rows) > int(policy["source_page_size"]):
        raise ValueError("NSF has no bounded award list.")
    observations, seen = [], set()
    rejected = {"invalid_record": 0, "outside_award_interval": 0, "query_terms": 0, "duplicate": 0}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid NSF award record.")
        identifier = str(row.get("id") or "")
        title = row.get("title")
        day = _day(row.get("date") or row.get("initAmendmentDate"))
        if not re.fullmatch(r"\d{6,10}", identifier) or not isinstance(title, str) or not title.strip() or not day:
            rejected["invalid_record"] += 1
            continue
        if not start.isoformat() <= day <= end.isoformat():
            rejected["outside_award_interval"] += 1
            continue
        searchable = title + " " + str(row.get("abstractText") or "")
        if not all_terms_match(query.query, searchable):
            rejected["query_terms"] += 1
            continue
        if identifier in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(identifier)
        project_start = _day(row.get("startDate"))
        country = row.get("awardeeCountryCode")
        observations.append({
            "award_id": identifier, "title": title.strip()[:1000],
            "url": f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={identifier}",
            "award_date": day, "project_start_date": project_start,
            "project_end_date": _day(row.get("expDate")),
            "project_start_planned": project_start > end.isoformat() if project_start else None,
            "recipient_organisation": str(row.get("awardeeName") or row.get("awardee") or "")[:300] or None,
            "source_country": country if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) else None,
            "country_scope": "recipient_organisation_only", "publisher": "NSF Awards",
            "publisher_organisation": "NSF", "language": None,
            "estimated_total_amount_usd": amount(row.get("estimatedTotalAmt")),
            "obligated_amount_usd": amount(row.get("fundsObligatedAmt")),
            "funding_amount": amount(row.get("fundsObligatedAmt")), "funding_currency": "USD",
            "funding_amount_basis": "reported_obligation_not_paid_expenditure",
            "record_type": "research_grant_award", "award_type": row.get("transType"),
            "match_basis": "title_and_abstract_all_literal_terms", "independent_confirmation": False,
        })
    observations.sort(key=lambda item: (item["award_date"], item["award_id"]), reverse=True)
    result = base_report(query, retrieved_at, url, payload_bytes, http_status)
    result.update({
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations[:query.max_records],
        "observed_record_count": min(len(observations), query.max_records),
        "reported_total_results": total, "reported_total_scope": "API_query_before_local_eligibility_filter",
        "response_records_examined": len(rows), "rejected_records": rejected,
        "result_cap_reached": len(observations) > query.max_records or total is None or total > len(rows),
        "award_date_filter_applied": True, "strict_all_query_terms_filter_applied": True,
        "project_family_deduplication_applied": False, "contains_private_investment_deals": False,
    })
    if rows and rejected["invalid_record"] == len(rows):
        raise ValueError("All NSF rows lack a usable award identity or date.")
    return finish(result)


def fetch(query: NSFQuery, timeout: float = 12.0) -> dict:
    return fetch_bounded(query, request_url(query), parse_response, timeout=timeout)
