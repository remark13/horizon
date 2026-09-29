"""OpenAIRE Graph V3 funded-project metadata with explicit upstream provenance."""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from saia.external_sources import load_policy
from saia.public_metadata import MetadataQuery, all_terms_match, amount, base_report, fetch_bounded, finish, iso_day, json_payload, quoted_and


class OpenAIREProjectQuery(MetadataQuery):
    section = "openaire_projects"


def request_url(query: OpenAIREProjectQuery) -> str:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    return policy["search_url"] + "?" + urlencode({
        "search": quoted_and(query.query), "fromStartDate": start.isoformat(), "toStartDate": end.isoformat(),
        "page": 1, "pageSize": policy["source_page_size"], "sortBy": "startDate DESC",
    })


def parse_response(query: OpenAIREProjectQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    payload = json_payload(payload_bytes, query.section)
    rows, header = payload.get("results"), payload.get("header")
    if not isinstance(rows, list) or not isinstance(header, dict) or payload.get("error") or len(rows) > int(policy["source_page_size"]):
        raise ValueError("OpenAIRE returned an invalid bounded project response.")
    observations, seen = [], set()
    rejected = {"invalid_identity_or_date": 0, "outside_start_interval": 0, "query_terms": 0, "duplicate": 0}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid OpenAIRE project record.")
        identifier, title = row.get("id"), row.get("title")
        day = iso_day(row.get("startDate"))
        if not isinstance(identifier, str) or not re.fullmatch(r"[\w:.-]{2,300}", identifier) or not isinstance(title, str) or not title.strip() or not day:
            rejected["invalid_identity_or_date"] += 1
            continue
        if not start.isoformat() <= day <= end.isoformat():
            rejected["outside_start_interval"] += 1
            continue
        subjects = row.get("subjects") or []
        searchable = " ".join((title, str(row.get("summary") or ""), str(row.get("keywords") or ""), " ".join(str(v) for v in subjects)))
        if not all_terms_match(query.query, searchable):
            rejected["query_terms"] += 1
            continue
        if identifier in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(identifier)
        funders = [{key: value.get(key) for key in ("shortName", "name", "jurisdiction")}
                   for value in row.get("fundings") or [] if isinstance(value, dict)][:10]
        participants = [{"name": value.get("legalname") or value.get("legalshortname"),
                         "country": (value.get("country") or {}).get("code")}
                        for value in row.get("links") or [] if isinstance(value, dict)
                        and isinstance(value.get("header"), dict) and value["header"].get("relationClass") == "hasParticipant"
                        and isinstance(value.get("country") or {}, dict)][:25]
        code = str(row.get("code") or "")[:200] or None
        is_ec = any(f["shortName"] == "EC" for f in funders)
        record_url = f"https://cordis.europa.eu/project/id/{code}" if is_ec and code and re.fullmatch(r"\d{5,9}", code) else (
            "https://explore.openaire.eu/search/project?projectId=" + quote(identifier, safe=""))
        granted = row.get("granted") if isinstance(row.get("granted"), dict) else {}
        currency = granted.get("currency")
        currency = currency if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency) else None
        funded = amount(granted.get("fundedAmount"), allow_zero=False)
        observations.append({
            "project_id": identifier, "grant_reference": code, "acronym": row.get("acronym"),
            "title": title.strip()[:1000], "url": record_url, "project_start_date": day,
            "project_end_date": iso_day(row.get("endDate")), "funder_names": [v["name"] for v in funders if v["name"]],
            "funders": funders, "participants": participants,
            "participant_country_codes": sorted({v["country"] for v in participants if isinstance(v["country"], str) and re.fullmatch(r"[A-Z]{2}", v["country"])}),
            "publisher": "OpenAIRE Graph", "publisher_organisation": "OpenAIRE",
            "language": None, "source_country": None, "country_scope": "participant_organisations_only",
            "funding_amount": funded if currency else None, "funding_currency": currency,
            "funding_amount_basis": "reported_project_funding_not_paid_expenditure",
            "reported_zero_amount_is_ambiguous": amount(granted.get("fundedAmount")) == 0,
            "funding_currency_missing": funded is not None and currency is None,
            "record_type": "funded_research_project", "metadata_licence": "CC-BY",
            "upstream_grant_deduplication_applied": False, "independent_confirmation": False,
            "match_basis": "title_keywords_summary_subjects_all_literal_terms",
        })
    total = header.get("numFound")
    result = base_report(query, retrieved_at, url, payload_bytes, http_status)
    result.update({
        "request": {**result["request"], "schema": "OpenAIRE_Graph_V3"},
        "status": "complete" if observations else "empty_observed_response", "observations": observations[:query.max_records],
        "observed_record_count": min(len(observations), query.max_records),
        "reported_total_results": total if type(total) is int and total >= 0 else None,
        "reported_total_scope": "API_query_before_local_eligibility_filter", "response_records_examined": len(rows),
        "rejected_records": rejected, "result_cap_reached": len(observations) > query.max_records or type(total) is not int or total > len(rows),
        "strict_all_query_terms_filter_applied": True, "project_start_filter_applied": True,
        "stores_summaries": False, "upstream_grant_deduplication_applied": False,
        "contains_private_investment_deals": False, "metadata_licence": "CC-BY", "attribution": "OpenAIRE Graph",
    })
    if rows and rejected["invalid_identity_or_date"] == len(rows):
        raise ValueError("All OpenAIRE projects lack a usable identity or date.")
    return finish(result)


def fetch(query: OpenAIREProjectQuery, timeout: float = 12.0) -> dict:
    return fetch_bounded(query, request_url(query), parse_response, timeout=timeout)
