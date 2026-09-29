"""Read-only exact search over pinned, query-specific OpenAlex cohorts.

Prepared cohorts supplement discovery; they are not a complete field index and
must not replace live OpenAlex for an arbitrary free-form request.
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import date
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from saia.compiled_phrase_matching import matches_spec
from saia.controlled_collection import sha256_file
from saia.discovery import Publication
from saia.openalex_work_variants import collapse


VERSION = "prepared-openalex-query-cohorts-exact-search-v1"
ADMINISTRATIVE_TYPES = frozenset({
    "peer-review", "editorial", "paratext", "erratum", "retraction",
    "supplementary-materials", "reference-entry", "conference-abstract",
})


def _title_key(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).strip()


def _select_year_spread(rows: list[dict], limit: int) -> list[dict]:
    years = sorted({row["publication_date"][:4] for row in rows}, reverse=True)
    queues = {year: deque(sorted(
        (row for row in rows if row["publication_date"].startswith(year)),
        key=lambda item: (
            hashlib.sha256(item["openalex_id"].encode()).hexdigest(),
            item["openalex_id"]))) for year in years}
    result = []
    while len(result) < limit and any(queues.values()):
        for year in years:
            if queues[year] and len(result) < limit:
                result.append(queues[year].popleft())
    return result


def _publication(row: dict) -> dict:
    doi = str(row.get("doi") or "").strip().casefold() or None
    title = " ".join((row.get("title") or "").split())
    url = row["openalex_url"]
    result = asdict(Publication(
        canonical_key=f"doi:{doi}" if doi else f"title:{_title_key(title)}",
        title=title,
        abstract=" ".join((row.get("abstract") or "").split()) or None,
        published_at=row["publication_date"], sources=("openalex",),
        source_ids=(url,), urls=(url,), doi=doi,
        authors=tuple(row.get("authors") or ()),
        openalex_type=row.get("document_type"),
    ))
    result["prepared_openalex_variant_ids"] = row.get(
        "source_openalex_variant_ids", [row["openalex_id"]])
    result["prepared_family_earliest_publication_date"] = row.get(
        "family_earliest_publication_date", row["publication_date"])
    return result


def scan(directory: Path, branch_specs: list[dict], date_from: date,
         as_of_date: date, limit_per_branch: int) -> dict:
    import pyarrow.parquet as pq

    if (date_from >= as_of_date or not 1 <= limit_per_branch <= 100
            or not branch_specs or len(branch_specs) > 12):
        raise ValueError("Invalid prepared OpenAlex search bounds")
    branch_ids = [spec.get("branch_id") for spec in branch_specs]
    if any(not value for value in branch_ids) or len(set(branch_ids)) != len(branch_ids):
        raise ValueError("Prepared OpenAlex branches must have unique IDs")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != "priority-openalex-complete-cohorts-v2"
            or manifest.get("policy", {}).get(
                "only_verified_query_complete_saved_pages") is not True
            or any(item.get("source_errors") for item in manifest.get("cohorts", []))):
        raise ValueError("Prepared OpenAlex source is not a verified complete query cohort")
    file_info = manifest["file"]
    file_name = file_info["name"]
    if not isinstance(file_name, str) or Path(file_name).name != file_name:
        raise ValueError("Prepared OpenAlex file must be inside its cohort directory")
    path = directory / file_name
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("Prepared OpenAlex file escapes its cohort directory")
    if (path.stat().st_size != file_info["bytes"]
            or sha256_file(path) != file_info["sha256"]):
        raise ValueError("Prepared OpenAlex cohort differs from pinned manifest")
    all_rows = pq.read_table(path, columns=[
        "openalex_id", "openalex_url", "doi", "title", "abstract",
        "publication_date", "document_type", "authors", "source_mission_ids",
    ]).to_pylist()
    in_period = [row for row in all_rows
                 if date_from.isoformat() <= row["publication_date"]
                 < as_of_date.isoformat()]
    eligible = [row for row in in_period
                if row["document_type"] not in ADMINISTRATIVE_TYPES]
    canonical, family_audit = collapse(eligible)
    by_id = {row["openalex_id"]: row for row in eligible}
    branches = []
    for spec in branch_specs:
        matches = []
        for representative in canonical:
            family_ids = representative.get(
                "source_openalex_variant_ids", [representative["openalex_id"]])
            matching_version = next((by_id[identifier] for identifier in family_ids
                                     if matches_spec(by_id[identifier], spec)), None)
            if matching_version is None:
                continue
            # A later abstract can be the first version containing the phrase.
            # Do not attach that text to the earlier representative's date.
            matched = dict(matching_version)
            matched["source_openalex_variant_ids"] = family_ids
            matched["source_mission_ids"] = representative["source_mission_ids"]
            matched["family_earliest_publication_date"] = representative["publication_date"]
            matches.append(matched)
        selected = _select_year_spread(matches, limit_per_branch)
        branches.append({
            "branch_id": spec["branch_id"],
            "eligible_matches_in_prepared_query_cohorts": len(matches),
            "year_counts": dict(sorted(Counter(
                row["publication_date"][:4] for row in matches).items())),
            "selected_year_counts": dict(sorted(Counter(
                row["publication_date"][:4] for row in selected).items())),
            "works": [_publication(row) for row in selected],
            "selected_source_mission_ids": sorted({mission for row in selected
                                                   for mission in row[
                                                       "source_mission_ids"]}),
        })
    return {
        "version": VERSION,
        "source": "pinned_query_specific_openalex_cohorts",
        "manifest_sha256": sha256_file(manifest_path),
        "parquet_sha256": file_info["sha256"],
        "available_unique_ids": len(all_rows),
        "date_from": date_from.isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "candidate_rows_in_period": len(in_period),
        "administrative_rows_excluded": len(in_period) - len(eligible),
        "variant_rows_collapsed": family_audit["variant_rows_collapsed"],
        "cohort_ids": [item["mission_id"] for item in manifest["cohorts"]],
        "latest_source_fetch_finished_utc": max(
            (item["fetch_finished_utc"] for item in manifest["cohorts"]),
            default=None),
        "branches": branches,
        "coverage_comparable": None,
        "complete_for_arbitrary_query": False,
        "limitations": [
            "Only works already present in saved OpenAlex query cohorts can match; zero is not evidence of absence.",
            "This read-only cache is supplemental and must not replace live search for an arbitrary query.",
            "Current title and abstract text and publication dates do not establish historical wording or first mention.",
            "Administrative document types and conservative exact-title-author variants were excluded from this retrieval view; raw pages remain unchanged.",
            "Citation, market, patent and independent-team evidence is not measured here.",
        ],
    }
