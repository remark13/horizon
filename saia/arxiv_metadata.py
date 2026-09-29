"""Read-only adapter for the Cornell/Kaggle arXiv metadata mirror.

The first submission comes from v1, never from update_date. Version dates
are retained, but the mirror does not contain the separate historical texts.
"""

from __future__ import annotations

import re
from datetime import date, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

ADAPTER_VERSION = "arxiv-metadata-local-0.4.3"
LITERAL_MATCHING_VERSION = "literal-phrase-0.4.6"
ORTHOGRAPHIC_MATCHING_VERSION = "orthographic-separators-v1"
ORTHOGRAPHIC_SEPARATOR_PATTERN = r"[-‐‑‒–—―−_/]+"
REQUIRED_COLUMNS = {"id", "title", "abstract", "categories", "versions"}
IDENTIFIER = re.compile(r"(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.-]*/\d{7})")


def text_scope(mission: dict) -> dict | None:
    """Explicit opt-in; general query terms never silently change old cohorts."""
    scope = (mission.get("query") or {}).get("arxiv_local_text_scope")
    if scope is None:
        return None
    if (not isinstance(scope, dict) or set(scope) != {"mode", "fields", "phrases"}
            or scope.get("mode") != "any_exact_phrase"
            or scope.get("fields") != ["title", "abstract"]
            or not isinstance(scope.get("phrases"), list) or not scope["phrases"]
            or any(not isinstance(p, str) or not p.strip() or not re.search(r"\w", p)
                   for p in scope["phrases"])):
        raise ValueError("Invalid arxiv_local_text_scope; require any_exact_phrase, title/abstract and nonempty phrases")
    return {"mode": scope["mode"], "fields": list(scope["fields"]),
            "phrases": list(scope["phrases"]), "matching_version": "literal-phrase-0.4.6",
            "normalization": "casefold_and_collapsed_whitespace; word_boundaries; no_synonyms"}


def matches_text_scope(row: dict, mission: dict) -> bool:
    scope = text_scope(mission)
    if scope is None:
        return True
    # Match within either field, not across the title/abstract boundary.
    texts = [" ".join(str(row.get(field) or "").casefold().split()) for field in scope["fields"]]
    return any(re.search(r"(?<!\w)" + re.escape(" ".join(phrase.casefold().split())) + r"(?!\w)", text)
               for phrase in scope["phrases"] for text in texts)


def normalized_phrase(value: str, matching_version: str = LITERAL_MATCHING_VERSION) -> str:
    """Normalize spelling, without stemming, synonym expansion or reordering."""
    value = str(value or "").casefold()
    if matching_version == ORTHOGRAPHIC_MATCHING_VERSION:
        value = re.sub(ORTHOGRAPHIC_SEPARATOR_PATTERN, " ", value)
    elif matching_version != LITERAL_MATCHING_VERSION:
        raise ValueError("Unsupported phrase matching version")
    return " ".join(value.split())


def _matches_phrase(text: str, phrase: str,
                    matching_version: str = LITERAL_MATCHING_VERSION) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(normalized_phrase(phrase, matching_version))
                          + r"(?!\w)", normalized_phrase(text, matching_version)))


def controlled_plan(mission: dict) -> dict | None:
    """Return an approved literal plan, never infer one from loose query terms."""
    plan = mission.get("controlled_search_plan")
    if plan is None:
        return None
    included = plan.get("included_terms") if isinstance(plan, dict) else None
    excluded = plan.get("exclusions") if isinstance(plan, dict) else None
    if (not isinstance(included, list) or not included
            or not isinstance(excluded, list)
            or any(not isinstance(value, str) or not value.strip()
                   for value in [*included, *excluded])):
        raise ValueError("Invalid controlled_search_plan literal terms")
    return plan


def matches_controlled_plan(row: dict, mission_or_plan: dict) -> bool:
    """Apply the approved field-local predicate, preserving legacy plan semantics."""
    plan = (mission_or_plan if "included_terms" in mission_or_plan
            else controlled_plan(mission_or_plan))
    if plan is None:
        return True
    texts = [str(row.get(field) or "") for field in ("title", "abstract")]
    included = list(plan["included_terms"])
    excluded = list(plan.get("exclusions") or [])
    matching_version = plan.get("matching_version", LITERAL_MATCHING_VERSION)
    if matching_version not in (LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION):
        raise ValueError("Unsupported phrase matching version")
    return (any(_matches_phrase(text, phrase, matching_version)
                for phrase in included for text in texts)
            and not any(_matches_phrase(text, phrase, matching_version)
                        for phrase in excluded for text in texts))


def validate_schema(names) -> None:
    missing = REQUIRED_COLUMNS - set(names)
    if missing:
        raise ValueError("Unsupported arXiv metadata schema; missing: " + ", ".join(sorted(missing)))


def selection(mission: dict) -> tuple[set[str], str | None, str | None]:
    categories = set((mission.get("query") or {}).get("arxiv_categories") or [])
    period = mission.get("period") or {}
    start, end = period.get("from"), period.get("to")
    for value in (start, end):
        if value is not None:
            date.fromisoformat(value)
    if start and end and start > end:
        raise ValueError("Empty arXiv selection period")
    return categories, start, end


def _instant(value):
    try:
        instant = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Invalid arXiv version date") from error
    if instant is None or instant.tzinfo is None:
        raise ValueError("arXiv version date must specify a timezone")
    return instant.astimezone(timezone.utc)


def first_submission(versions) -> str:
    if not isinstance(versions, list):
        raise ValueError("arXiv v1 missing; first submission cannot be inferred")
    first = [item for item in versions if isinstance(item, dict) and item.get("version") == "v1"]
    if len(first) != 1:
        raise ValueError("Exactly one arXiv v1 is required")
    return _instant(first[0].get("created", "")).date().isoformat()


def version_dates(versions) -> tuple[str, str]:
    if not isinstance(versions, list) or not versions:
        raise ValueError("arXiv metadata has no version history; v1 cannot be inferred")
    parsed = {}
    for version in versions:
        if not isinstance(version, dict) or not re.fullmatch(r"v[1-9]\d*", str(version.get("version", ""))):
            raise ValueError("Invalid arXiv version identity")
        number = int(version["version"][1:])
        if number in parsed:
            raise ValueError("Duplicate arXiv version identity")
        parsed[number] = _instant(version.get("created", ""))
    if 1 not in parsed:
        raise ValueError("arXiv v1 missing; first submission cannot be inferred")
    ordered = [parsed[key] for key in sorted(parsed)]
    if ordered != sorted(ordered):
        raise ValueError("arXiv version dates contradict version order")
    return parsed[1].date().isoformat(), ordered[-1].date().isoformat()


def selected(row: dict, mission: dict) -> bool:
    wanted, start, end = selection(mission)
    categories = str(row.get("categories") or "").split()
    if wanted and not wanted.intersection(categories):
        return False
    if not matches_text_scope(row, mission):
        return False
    if not matches_controlled_plan(row, mission):
        return False
    # A corrupt later revision of a paper outside the requested period must
    # not block an unrelated cohort. Full history is checked for selected rows.
    created = first_submission(row.get("versions"))
    return not ((start and created < start) or (end and created > end))


def to_record(row: dict, *, filename: str, revision: str | None = None) -> tuple[str, dict]:
    identifier = str(row.get("id") or "").strip()
    if not IDENTIFIER.fullmatch(identifier):
        raise ValueError("Invalid arXiv metadata identifier: " + identifier)
    created, revised = version_dates(row.get("versions"))
    authors = []
    parsed_authors = row.get("authors_parsed") or []
    if not isinstance(parsed_authors, list):
        raise ValueError("Invalid parsed arXiv authors")
    for author in parsed_authors:
        if not isinstance(author, list) or not all(isinstance(part, str) for part in author):
            raise ValueError("Invalid parsed arXiv author")
        # Source convention: surname, forenames, optional suffix. Do not
        # split the free-form authors string or invent affiliations/IDs.
        if author and author[0].strip():
            name = " ".join(part.strip() for part in [*author[1:2], author[0], *author[2:]] if part.strip())
            authors.append({"name": name})
    metadata_update = row.get("update_date")
    return identifier, {
        "id": identifier, "created": created, "updated": revised,
        "title": row.get("title") or "", "summary": row.get("abstract") or "",
        "categories": str(row.get("categories") or "").split(),
        "author": authors,
        "arxiv_doi": row.get("doi") or "",
        "arxiv_journal_ref": row.get("journal-ref") or "",
        "arxiv_comment": row.get("comments") or "",
        "arxiv_primary_category": None,  # not explicitly identified in this schema
        "_source_format": "hf-arxiv-parquet-snapshot",
        "_snapshot_schema": "arxiv-metadata-oai",
        "_adapter_version": ADAPTER_VERSION,
        "_snapshot_file": filename, "_snapshot_revision": revision,
        "_arxiv_versions": row["versions"],
        "_metadata_update_date_raw": metadata_update.isoformat() if hasattr(metadata_update, "isoformat") else metadata_update,
        "_authors_raw": row.get("authors") or "",
        "_author_parse_status": "parsed_names_only" if authors else "unresolved",
        "_article_license": row.get("license"),
        "_historical_text_scope": "current_metadata_not_recovered_versions",
    }


def records(path: Path, mission: dict | None = None) -> Iterator[tuple[str, dict]]:
    import pyarrow.parquet as pq

    mission = mission or {}
    selection(mission)
    parquet = pq.ParquetFile(path)
    validate_schema(parquet.schema_arrow.names)
    revision = ((mission.get("query") or {}).get("arxiv_local_snapshot") or {}).get("revision")
    for batch in parquet.iter_batches(batch_size=4096):
        for row in batch.to_pylist():
            if selected(row, mission):
                yield to_record(row, filename=path.name, revision=revision)
