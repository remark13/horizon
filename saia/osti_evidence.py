"""Bounded OSTI bibliography for human inspection, not model input or a corpus.

OSTI asks AI/ML/LLM projects and bulk reuse to coordinate separately. This
adapter only keeps a few attributed title/date/identifier records for the
source drawer. Never keep abstracts, fetch full texts or infer peer review.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from saia.external_sources import load_policy
from saia.public_metadata import MetadataQuery, all_terms_match, base_report, fetch_bounded, finish, iso_day, quoted_and

PRODUCT_TYPES = {
    "Journal Article": "journal_article",
    "Technical Report": "research_report",
    "Conference": "conference_publication",
    "Thesis/Dissertation": "research_thesis",
    "Book": "research_book",
}


class OSTIQuery(MetadataQuery):
    section = "osti_records"


def request_url(query: OSTIQuery) -> str:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    return policy["search_url"] + "?" + urlencode({
        "title": quoted_and(query.query),
        "publication_date_start": start.strftime("%m/%d/%Y"),
        "publication_date_end": end.strftime("%m/%d/%Y"),
        "rows": policy["source_page_size"], "page": 1,
        "sort": "publication_date", "order": "desc",
    })


def _text(value: object, maximum: int = 300) -> str | None:
    return value.strip()[:maximum] if isinstance(value, str) and value.strip() else None


def _identifier(value: object) -> str | None:
    if type(value) is int and value > 0:
        value = str(value)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,14}", value) and int(value) > 0 else None


def _doi(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("https://doi.org/"):
        value = value[len("https://doi.org/"):]
    return value.casefold() if len(value) <= 500 and re.fullmatch(r"10\.[0-9]{4,9}/[^\s]+", value) and not any(ord(c) < 32 for c in value) else None


def parse_response(query: OSTIQuery, payload_bytes: bytes, retrieved_at: str,
                   url: str, http_status: int = 200) -> dict:
    start, end, _ = query.validate()
    policy = load_policy()[query.section]
    if len(payload_bytes) > int(policy["max_response_bytes"]):
        raise ValueError("Ответ OSTI превысил ограниченный размер.")
    def reject_constant(_):
        raise ValueError("Недопустимая числовая константа в JSON OSTI.")
    rows = json.loads(payload_bytes, parse_constant=reject_constant)
    if not isinstance(rows, list) or len(rows) > int(policy["source_page_size"]):
        raise ValueError("OSTI должен вернуть ограниченный массив записей.")
    observations, seen, rejected = [], set(), {}
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Некорректная запись OSTI.")
        identifier, title = _identifier(row.get("osti_id")), _text(row.get("title"), 1000)
        if not identifier or not title:
            reject("missing_identity_or_title")
            continue
        product_type = row.get("product_type")
        if not isinstance(product_type, str) or product_type not in PRODUCT_TYPES:
            reject("not_supported_publication_type")
            continue
        published = iso_day(row.get("publication_date"))
        if not published:
            reject("unknown_or_invalid_publication_date")
            continue
        if not start.isoformat() <= published <= end.isoformat():
            reject("outside_publication_interval")
            continue
        if not all_terms_match(query.query, title):
            reject("no_visible_title_match")
            continue
        if identifier in seen:
            reject("duplicate_osti_id")
            continue
        seen.add(identifier)
        authors = row.get("authors")
        author_names = [name for value in authors[:10] if (name := _text(value, 160))] if isinstance(authors, list) else []
        observations.append({
            "osti_id": identifier, "title": title,
            "url": f"https://www.osti.gov/biblio/{identifier}",
            "doi": _doi(row.get("doi")), "publication_date": published,
            "entry_date": iso_day(row.get("entry_date")),
            "date_basis": "source_reported_publication_not_entry_date",
            "date_precision": "day", "publication_date_accuracy_verified": False,
            "product_type": product_type, "record_type": PRODUCT_TYPES[product_type],
            "journal_name": _text(row.get("journal_name")),
            "publisher": _text(row.get("publisher")) or "OSTI.GOV",
            "publisher_organisation": "DOE OSTI", "author": "; ".join(author_names) or None,
            "language": _text(row.get("language"), 80),
            "source_country": _text(row.get("country_publication"), 100),
            "country_scope": "country_of_publication_not_authors_or_adoption",
            "match_basis": "all_literal_query_terms_in_title",
            "independent_confirmation": False, "peer_review_verified": False,
            "model_input_allowed": False,
            "metadata_reuse_scope": "bounded_attributed_bibliographic_inspection_only",
        })
    observations.sort(key=lambda item: (item["publication_date"], item["osti_id"]), reverse=True)
    result = base_report(query, retrieved_at, url, payload_bytes, http_status)
    result.update({
        "status": "complete" if observations else "empty_observed_response",
        "observations": observations[:query.max_records],
        "observed_record_count": min(len(observations), query.max_records),
        "response_records_examined": len(rows), "rejected_records": rejected,
        "reported_total_results": None, "source_total_not_available_in_body": True,
        "result_cap_reached": len(rows) >= int(policy["source_page_size"]) or len(observations) > query.max_records,
        "strict_visible_title_filter_applied": True, "stores_descriptions": False,
        "overlap_with_openalex_requires_dedup": True,
        "independent_publication_count_established": False,
        "research_family_deduplication_applied": False,
        "model_input_allowed": False, "model_training_allowed": False,
        "model_inputs_modified": False, "bulk_reuse_approved": False,
    })
    if rows and rejected.get("missing_identity_or_title", 0) == len(rows):
        raise ValueError("Все записи OSTI лишены пригодной идентичности или названия.")
    return finish(result)


def fetch(query: OSTIQuery, timeout: float = 12.0) -> dict:
    result = fetch_bounded(query, request_url(query), parse_response, timeout=timeout)
    # Error observations retain the same no-model-input boundary.
    result.pop("report_payload_sha256", None)
    result.update(model_input_allowed=False, model_training_allowed=False,
                  model_inputs_modified=False, bulk_reuse_approved=False)
    return finish(result)
