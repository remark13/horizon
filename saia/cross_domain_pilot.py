"""One-pass local-arXiv retrieval pilot across nine internal focus profiles.

The output measures exact-phrase retrieval and creates an unlabelled review
packet. It does not classify weak signals or estimate product quality.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
import httpx

from saia.arxiv_metadata import first_submission, matches_controlled_plan, to_record
from saia.focus_areas import EXPECTED_IDS
from saia.local_arxiv_search import policy as arxiv_policy, validate_inventory
from saia.discovery import _openalex_publications, collect_openalex


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "cross-domain-pilot.v0.4.34.yaml"
VERSION = "cross-domain-retrieval-pilot-0.4.34"


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_pilot(path: str | Path = CONFIG_PATH) -> dict:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") != VERSION:
        raise ValueError("Unsupported cross-domain pilot version")
    profiles = payload.get("profiles") or []
    if {item.get("profile_id") for item in profiles} != EXPECTED_IDS or len(profiles) != 9:
        raise ValueError("Pilot must contain the nine internal focus profiles")
    cases = [case for item in profiles for case in item.get("cases") or []]
    if len(cases) != 27 or len({case.get("case_id") for case in cases}) != 27:
        raise ValueError("Pilot must contain 27 unique cases")
    for item in profiles:
        scopes = [case.get("scope") for case in item["cases"]]
        if len(scopes) != 3 or scopes.count("broad") != 1 or scopes.count("subarea") != 2:
            raise ValueError(f"{item['profile_id']} must contain one broad and two subarea cases")
    for case in cases:
        terms = case.get("included_terms") or []
        if not terms or any(not isinstance(term, str) or len(term.strip()) < 3 for term in terms):
            raise ValueError(f"{case.get('case_id')} has invalid included terms")
        if case.get("scope") not in {"broad", "subarea"}:
            raise ValueError(f"{case.get('case_id')} has invalid scope")
    start = date.fromisoformat(payload["period"]["date_from"])
    cutoff = date.fromisoformat(payload["period"]["as_of_date"])
    if start >= cutoff:
        raise ValueError("Pilot period is invalid")
    if payload["interpretation"].get("weak_signal_labels_present") is not False:
        raise ValueError("Retrieval pilot cannot contain weak-signal labels")
    return payload


def _all_cases(pilot: dict) -> list[dict]:
    return [
        {"profile_id": profile["profile_id"], **case}
        for profile in pilot["profiles"]
        for case in profile["cases"]
    ]


def scan_local_arxiv(directory: str | Path, pilot: dict | None = None,
                     *, expected_files: int | None = None,
                     expected_rows: int | None = None) -> dict:
    """Scan every pinned shard once and retain one deterministic item per year."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    pilot = pilot or load_pilot()
    cases = _all_cases(pilot)
    arxiv_cfg = arxiv_policy()
    expected_files = expected_files or arxiv_cfg["expected_files"]
    expected_rows = expected_rows or arxiv_cfg["expected_rows"]
    files = validate_inventory(str(Path(directory).resolve()), expected_files, expected_rows)
    start = date.fromisoformat(pilot["period"]["date_from"])
    cutoff = date.fromisoformat(pilot["period"]["as_of_date"])
    stats = {
        case["case_id"]: {
            "profile_id": case["profile_id"], "case_id": case["case_id"],
            "scope": case["scope"], "label_ru": case["label_ru"],
            "included_terms": case["included_terms"],
            "exclusions": case.get("exclusions") or [],
            "coarse_matches": 0, "exact_matches_all_dates": 0,
            "eligible_matches": 0, "invalid_dates": 0,
            "year_counts": {}, "sample_by_year": {},
        }
        for case in cases
    }
    columns = [
        "id", "title", "abstract", "categories", "versions", "authors_parsed",
        "doi", "journal-ref", "comments", "update_date", "authors", "license",
    ]
    scanned_rows = 0
    for path in files:
        parquet = pq.ParquetFile(path)
        available = set(parquet.schema_arrow.names)
        wanted = [column for column in columns if column in available]
        for batch in parquet.iter_batches(batch_size=8192, columns=wanted):
            scanned_rows += batch.num_rows
            table = pa.Table.from_batches([batch])
            # Normalize the two text columns once per batch. The first version
            # repeated these relatively expensive Arrow operations for every
            # case and made a 27-case development scan unreasonably slow.
            normalized_fields = {
                field: pc.replace_substring_regex(
                    pc.utf8_lower(pc.fill_null(table[field], "")),
                    pattern=r"\s+", replacement=" ",
                )
                for field in ("title", "abstract")
            }
            term_masks = {}
            for case in cases:
                current = stats[case["case_id"]]
                mask = None
                for term in case["included_terms"]:
                    needle = " ".join(term.casefold().split())
                    if needle not in term_masks:
                        term_masks[needle] = pc.or_kleene(
                            pc.match_substring(normalized_fields["title"], needle),
                            pc.match_substring(normalized_fields["abstract"], needle),
                        )
                    mask = term_masks[needle] if mask is None else pc.or_kleene(
                        mask, term_masks[needle]
                    )
                rows = table.filter(pc.fill_null(mask, False)).to_pylist()
                current["coarse_matches"] += len(rows)
                plan = {
                    "included_terms": case["included_terms"],
                    "exclusions": case.get("exclusions") or [],
                }
                for row in rows:
                    if not matches_controlled_plan(row, plan):
                        continue
                    current["exact_matches_all_dates"] += 1
                    try:
                        published = date.fromisoformat(first_submission(row.get("versions")))
                        if not start <= published < cutoff:
                            continue
                        identifier, record = to_record(
                            row, filename=path.name, revision=arxiv_cfg["revision"]
                        )
                    except ValueError:
                        current["invalid_dates"] += 1
                        continue
                    current["eligible_matches"] += 1
                    year = str(published.year)
                    current["year_counts"][year] = current["year_counts"].get(year, 0) + 1
                    rank = hashlib.sha256(
                        f"{case['case_id']}|{identifier}".encode("utf-8")
                    ).hexdigest()
                    previous = current["sample_by_year"].get(year)
                    if previous is None or rank < previous["selection_hash"]:
                        current["sample_by_year"][year] = {
                            "selection_hash": rank,
                            "arxiv_id": identifier,
                            "url": f"https://arxiv.org/abs/{identifier}",
                            "title": " ".join(str(record["title"]).split()),
                            "abstract": " ".join(str(record["summary"]).split()),
                            "first_submission_date": record["created"],
                            "categories": list(record["categories"]),
                        }
    results = []
    for case in cases:
        value = stats[case["case_id"]]
        samples = [value["sample_by_year"][year] for year in sorted(value["sample_by_year"])]
        del value["sample_by_year"]
        value["year_counts"] = {
            str(year): value["year_counts"].get(str(year), 0)
            for year in range(start.year, cutoff.year + 1)
            if date(year, 1, 1) < cutoff
        }
        value["review_sample"] = samples
        results.append(value)
    report = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pilot_config_sha256": _digest(pilot),
        "source": {
            "id": "local_arxiv_mirror", "revision": arxiv_cfg["revision"],
            "inventory_files": len(files), "inventory_rows": expected_rows,
            "scanned_rows": scanned_rows,
        },
        "period": pilot["period"],
        "selection": pilot["sampling"],
        "interpretation": {
            "retrieval_relevance_only": True,
            "publication_counts_are_retrieved_counts_not_signal_strength": True,
            "weak_signal_assessment_performed": False,
            "coverage_comparable_to_openalex": None,
        },
        "cases": results,
    }
    report["report_payload_sha256"] = _digest(report)
    return report


def build_review_artifacts(report: dict) -> tuple[dict, dict]:
    items = []
    for case in report["cases"]:
        for sample in case["review_sample"]:
            item_id = "item-" + hashlib.sha256(
                f"{case['case_id']}|{sample['arxiv_id']}".encode("utf-8")
            ).hexdigest()[:16]
            items.append({
                "item_id": item_id,
                "target_topic": case["label_ru"],
                "target_query_terms": case["included_terms"],
                "title": sample["title"],
                "abstract": sample["abstract"],
                "first_submission_date": sample["first_submission_date"],
                "categories": sample["categories"],
                "source_url": sample["url"],
            })
    items.sort(key=lambda item: item["item_id"])
    packet = {
        "version": "cross-domain-relevance-review-packet-0.4.34",
        "source_report_sha256": report["report_payload_sha256"],
        "purpose": "Оценка тематической релевантности найденных публикаций.",
        "not_assessed": ["слабый сигнал", "будущий успех", "рынок", "качество ранжирования"],
        "blinding": {
            "retrieval_counts_hidden": True,
            "year_series_hidden": True,
            "other_cases_hidden": True,
            "gold_labels_present": False,
        },
        "items": items,
    }
    packet["packet_payload_sha256"] = _digest(packet)
    template = {
        "version": "cross-domain-relevance-review-submission-0.4.34",
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "reviewer_id": "",
        "independent_review_declared": False,
        "annotations": [
            {
                "item_id": item["item_id"],
                "topical_relevance": "",
                "terminology_role": "",
                "rationale": "",
            }
            for item in items
        ],
        "allowed_values": {
            "topical_relevance": ["relevant", "partly_relevant", "not_relevant", "cannot_assess"],
            "terminology_role": ["direct_subject", "method_or_application", "background_mention", "cannot_assess"],
        },
    }
    return packet, template


def _openalex_expression(case: dict) -> str:
    expression = "(" + " OR ".join(f'"{term}"' for term in case["included_terms"]) + ")"
    exclusions = case.get("exclusions") or []
    if exclusions:
        expression += " NOT (" + " OR ".join(f'"{term}"' for term in exclusions) + ")"
    return expression


def collect_openalex_pilot(client: httpx.Client, pilot: dict | None = None,
                           *, limit: int = 5, pause_seconds: float = 1.5) -> dict:
    """Collect a bounded ranked channel; stop after the first rate limit."""
    if not 1 <= limit <= 25:
        raise ValueError("OpenAlex pilot limit must be between 1 and 25")
    pilot = pilot or load_pilot()
    start = date.fromisoformat(pilot["period"]["date_from"])
    cutoff = date.fromisoformat(pilot["period"]["as_of_date"])
    results = []
    halted = False
    for case in _all_cases(pilot):
        expression = _openalex_expression(case)
        if halted:
            results.append({
                "profile_id": case["profile_id"], "case_id": case["case_id"],
                "scope": case["scope"], "label_ru": case["label_ru"],
                "query_expression": expression, "request_status": "not_attempted_after_rate_limit",
                "observed_records": None, "works": [],
            })
            continue
        try:
            rows = collect_openalex(
                client, case["included_terms"][0], start, cutoff, limit, expression
            )
            works = _openalex_publications(rows, cutoff)
            results.append({
                "profile_id": case["profile_id"], "case_id": case["case_id"],
                "scope": case["scope"], "label_ru": case["label_ru"],
                "query_expression": expression, "request_status": "succeeded",
                "observed_records": len(works),
                "works": [
                    {
                        "title": work.title, "published_at": work.published_at,
                        "abstract": work.abstract,
                        "url": work.urls[0] if work.urls else None, "doi": work.doi,
                    }
                    for work in works
                ],
            })
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            results.append({
                "profile_id": case["profile_id"], "case_id": case["case_id"],
                "scope": case["scope"], "label_ru": case["label_ru"],
                "query_expression": expression, "request_status": "rate_limited" if status == 429 else "failed",
                "http_status": status, "observed_records": None, "works": [],
            })
            halted = status == 429
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            results.append({
                "profile_id": case["profile_id"], "case_id": case["case_id"],
                "scope": case["scope"], "label_ru": case["label_ru"],
                "query_expression": expression, "request_status": "failed",
                "error_type": type(error).__name__, "observed_records": None, "works": [],
            })
        if not halted and pause_seconds:
            time.sleep(pause_seconds)
    report = {
        "version": "cross-domain-openalex-bounded-pilot-0.4.34",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pilot_config_sha256": _digest(pilot),
        "period": pilot["period"],
        "selection": {
            "type": "current_ranked_api_preview", "limit_per_case": limit,
            "representative_sample": False, "comparable_to_local_arxiv_sample": False,
        },
        "interpretation": {
            "availability_and_query_diagnostic_only": True,
            "missing_or_unattempted_is_zero": False,
            "weak_signal_assessment_performed": False,
        },
        "cases": results,
    }
    report["report_payload_sha256"] = _digest(report)
    return report


def build_openalex_review_artifacts(report: dict) -> tuple[dict, dict]:
    items = []
    for case in report["cases"]:
        if case["request_status"] != "succeeded":
            continue
        for work in case["works"]:
            identity = work.get("doi") or work.get("url") or work["title"]
            item_id = "item-" + hashlib.sha256(
                f"{case['case_id']}|{identity}".encode("utf-8")
            ).hexdigest()[:16]
            items.append({
                "item_id": item_id,
                "target_topic": case["label_ru"],
                "query_expression": case["query_expression"],
                "title": work["title"],
                "abstract": work.get("abstract"),
                "publication_date": work["published_at"],
                "source_url": work.get("url"),
            })
    items.sort(key=lambda item: item["item_id"])
    packet = {
        "version": "cross-domain-openalex-relevance-review-packet-0.4.34",
        "source_report_sha256": report["report_payload_sha256"],
        "purpose": "Оценка релевантности ограниченной ранжированной выдачи OpenAlex.",
        "not_assessed": ["полнота OpenAlex", "слабый сигнал", "будущий успех", "рынок"],
        "blinding": {
            "case_result_count_hidden": True,
            "rank_within_top_five_hidden": True,
            "gold_labels_present": False,
        },
        "items": items,
    }
    packet["packet_payload_sha256"] = _digest(packet)
    template = {
        "version": "cross-domain-openalex-relevance-review-submission-0.4.34",
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "reviewer_id": "",
        "independent_review_declared": False,
        "annotations": [
            {
                "item_id": item["item_id"], "topical_relevance": "",
                "terminology_role": "", "rationale": "",
            }
            for item in items
        ],
        "allowed_values": {
            "topical_relevance": ["relevant", "partly_relevant", "not_relevant", "cannot_assess"],
            "terminology_role": ["direct_subject", "method_or_application", "background_mention", "cannot_assess"],
        },
    }
    return packet, template
