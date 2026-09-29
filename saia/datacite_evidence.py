"""Public DOI metadata for datasets/software; never download resource contents."""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote, urlencode

from saia.external_sources import load_policy
from saia.public_metadata import MetadataQuery, all_terms_match, base_report, fetch_bounded, finish, iso_day, json_payload, quoted_and


class DataCiteQuery(MetadataQuery):
    section = "datacite_artifacts"


def request_url(query: DataCiteQuery) -> str:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    return policy["search_url"] + "?" + urlencode({
        "query": f"({quoted_and(query.query)}) AND publicationYear:[{start.year} TO {end.year}]",
        "resource-type-id": "dataset,software", "page[size]": policy["source_page_size"], "sort": "relevance",
    })


def _doi(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 500 or not re.fullmatch(r"10\.\d{4,9}/[^\s]+", value, re.IGNORECASE) or any(ord(c) < 32 for c in value):
        return None
    return value.casefold()


def _release(attributes: dict, start: date, end: date) -> tuple[str | None, str | None, str | None]:
    year = attributes.get("publicationYear")
    if type(year) is not int or not 1000 <= year <= 9999:
        return None, None, "unknown_publication_year"
    dates = attributes.get("dates")
    if dates is not None and not isinstance(dates, list):
        return None, None, "invalid_dates"
    issued = [item.get("date") for item in dates or [] if isinstance(item, dict) and item.get("dateType") == "Issued"]
    exact = {day for value in issued if (day := iso_day(value)) is not None}
    if len(exact) > 1 or exact and any(int(day[:4]) != year for day in exact):
        return None, None, "ambiguous_issue_date"
    if exact:
        day = next(iter(exact))
        return (day, "day", None) if start.isoformat() <= day <= end.isoformat() else (None, None, "outside_issue_interval")
    # No January 1 imputation and no substitution of DOI.created/registered.
    if start <= date(year, 1, 1) and date(year, 12, 31) <= end:
        return str(year), "year", None
    return None, None, "year_only_boundary_unknown"


def parse_response(query: DataCiteQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    payload = json_payload(payload_bytes, query.section)
    rows = payload.get("data")
    if payload.get("errors") or not isinstance(rows, list) or len(rows) > int(policy["source_page_size"]):
        raise ValueError("DataCite returned errors or an invalid bounded DOI list.")
    observations, seen, rejected = [], set(), {}
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    for row in rows:
        attributes = row.get("attributes") if isinstance(row, dict) else None
        if not isinstance(attributes, dict):
            raise ValueError("Invalid DataCite DOI record.")
        doi = _doi(attributes.get("doi") or row.get("id"))
        types = attributes.get("types") or {}
        resource_type = types.get("resourceTypeGeneral") if isinstance(types, dict) else None
        if not doi or resource_type not in {"Dataset", "Software"} or attributes.get("state") != "findable" or attributes.get("isActive") is False:
            reject("invalid_identity_type_or_state")
            continue
        titles = attributes.get("titles")
        if not isinstance(titles, list) or not titles or not isinstance(titles[0], dict) or not isinstance(titles[0].get("title"), str) or not titles[0]["title"].strip():
            reject("missing_title")
            continue
        release, precision, reason = _release(attributes, start, end)
        if reason:
            reject(reason)
            continue
        searchable = " ".join(str(v.get(key) or "") for values, key in (
            (titles, "title"), (attributes.get("subjects") or [], "subject"),
            (attributes.get("descriptions") or [], "description"))
            for v in values if isinstance(v, dict))
        if not all_terms_match(query.query, searchable):
            reject("query_terms")
            continue
        if doi in seen:
            reject("duplicate_doi")
            continue
        seen.add(doi)
        publisher = attributes.get("publisher")
        if isinstance(publisher, dict):
            publisher = publisher.get("name")
        creators = attributes.get("creators") or []
        related = attributes.get("relatedIdentifiers") or []
        relations = [{"doi": d, "relation": v.get("relationType")} for v in related
                     if isinstance(v, dict) and v.get("relatedIdentifierType") == "DOI"
                     and (d := _doi(v.get("relatedIdentifier")))][:20]
        observations.append({
            "doi": doi, "title": titles[0]["title"].strip()[:1000],
            "url": "https://doi.org/" + quote(doi, safe="/"),
            "resource_type": resource_type, "record_type": "research_dataset" if resource_type == "Dataset" else "research_software",
            "resource_publication_date": release, "date_precision": precision,
            "publication_year": attributes["publicationYear"],
            "doi_created_at": attributes.get("created"), "doi_updated_at": attributes.get("updated"),
            "publisher": str(publisher)[:300] if publisher else None, "publisher_organisation": "DataCite metadata",
            "language": attributes.get("language") or None, "source_country": None,
            "country_scope": "not_verified", "author": "; ".join(str(c.get("name"))[:160] for c in creators[:10] if isinstance(c, dict) and c.get("name")) or None,
            "metadata_licence": "CC0", "resource_rights": [
                {key: v.get(key) for key in ("rights", "rightsUri", "rightsIdentifier") if v.get(key)}
                for v in (attributes.get("rightsList") or [])[:10] if isinstance(v, dict)],
            "related_dois": relations, "version": attributes.get("version"),
            "research_family_deduplication_applied": False, "independent_confirmation": False,
            "match_basis": "titles_subjects_descriptions_all_literal_terms",
        })
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    total = meta.get("total")
    result = base_report(query, retrieved_at, url, payload_bytes, http_status)
    result.update({
        "status": "complete" if observations else "empty_observed_response", "observations": observations[:query.max_records],
        "observed_record_count": min(len(observations), query.max_records),
        "reported_total_results": total if type(total) is int and total >= 0 else None,
        "reported_total_scope": "API_query_before_local_eligibility_filter", "response_records_examined": len(rows),
        "rejected_records": rejected, "result_cap_reached": len(observations) > query.max_records or type(total) is not int or total > len(rows),
        "strict_all_query_terms_filter_applied": True, "stores_descriptions": False,
        "research_family_deduplication_applied": False, "metadata_licence": "CC0", "resource_licence_inferred": False,
    })
    if rows and rejected.get("invalid_identity_type_or_state", 0) + rejected.get("missing_title", 0) == len(rows):
        raise ValueError("All DataCite records lack usable dataset/software metadata.")
    return finish(result)


def fetch(query: DataCiteQuery, timeout: float = 12.0) -> dict:
    return fetch_bounded(query, request_url(query), parse_response, timeout=timeout)
