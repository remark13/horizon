"""Bounded exact-phrase search over the pinned local arXiv mirror.

This is an availability fallback for the public API, not a historical text
archive.  It scans current metadata and uses the v1 date for the time filter.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from saia.arxiv_metadata import (LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION,
                                 ORTHOGRAPHIC_SEPARATOR_PATTERN, first_submission,
                                 matches_controlled_plan, normalized_phrase, to_record,
                                 validate_schema)
from saia.discovery import Publication


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "local-arxiv-fallback.v0.4.15.yaml"


@dataclass(frozen=True)
class LocalSearchResult:
    works: tuple[Publication, ...]
    audit: dict


def policy() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _title_key(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", _normalized(value)).strip()


def _doi_key(value: str) -> str:
    return _normalized(value).removeprefix("https://doi.org/").removeprefix("doi:").strip()


@functools.lru_cache(maxsize=4)
def validate_inventory(directory: str, expected_files: int, expected_rows: int) -> tuple[Path, ...]:
    import pyarrow.parquet as pq

    root = Path(directory).resolve()
    if not root.is_dir() or expected_files <= 0 or expected_rows <= 0:
        raise ValueError("Локальный снимок arXiv отсутствует или его паспорт некорректен.")
    files = tuple(sorted(root.glob("*.parquet")))
    names = [f"train-{index:05d}-of-{expected_files:05d}.parquet"
             for index in range(expected_files)]
    if [path.name for path in files] != names:
        raise ValueError("Набор shard-файлов локального arXiv не совпадает с паспортом.")
    rows = 0
    for path in files:
        parquet = pq.ParquetFile(path)
        validate_schema(parquet.schema_arrow.names)
        rows += parquet.metadata.num_rows
    if rows != expected_rows:
        raise ValueError("Число строк локального arXiv не совпадает с паспортом.")
    return files


def _substring_mask(table, phrases: list[str]):
    """Fast coarse filter; Python word-boundary check remains authoritative."""
    import pyarrow.compute as pc

    normalized = {field: _normalized_arrow(table[field])
                  for field in ("title", "abstract")}
    combined = None
    for phrase in phrases:
        needle = normalized_phrase(phrase, ORTHOGRAPHIC_MATCHING_VERSION)
        phrase_mask = None
        for field in ("title", "abstract"):
            field_mask = pc.match_substring(normalized[field], needle)
            phrase_mask = field_mask if phrase_mask is None else pc.or_kleene(phrase_mask, field_mask)
        combined = phrase_mask if combined is None else pc.or_kleene(combined, phrase_mask)
    return combined


def _normalized_arrow(column):
    """Coarse-filter superset for both historical literal and new plans."""
    import pyarrow.compute as pc

    value = pc.utf8_lower(pc.fill_null(column, ""))
    value = pc.replace_substring_regex(
        value, pattern=ORTHOGRAPHIC_SEPARATOR_PATTERN, replacement=" ")
    return pc.replace_substring_regex(value, pattern=r"\s+", replacement=" ")


def search(directory: str | Path, search_plan: dict, limit: int,
           *, expected_files: int | None = None,
           expected_rows: int | None = None) -> LocalSearchResult:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not 1 <= limit <= 100:
        raise ValueError("Лимит local arXiv должен быть от 1 до 100.")
    included = list(search_plan.get("included_terms") or [])
    excluded = list(search_plan.get("exclusions") or [])
    if not included:
        raise ValueError("В сохранённом плане нет включённых фраз.")
    start = date.fromisoformat(search_plan["date_from"])
    cutoff = date.fromisoformat(search_plan["as_of_date"])
    if start >= cutoff:
        raise ValueError("Некорректный период local arXiv.")
    cfg = policy()
    expected_files = expected_files if expected_files is not None else cfg["expected_files"]
    expected_rows = expected_rows if expected_rows is not None else cfg["expected_rows"]
    files = validate_inventory(str(Path(directory).resolve()), expected_files, expected_rows)
    # A complete immutable index may accelerate safe literal plans. It is a
    # prefilter, never a different matching policy. Unit fixtures and other
    # inventories must not inherit the worker's full-mirror index.
    if (expected_files, expected_rows) == (cfg["expected_files"], cfg["expected_rows"]):
        import json
        import os
        from saia.arxiv_trigram_index import search_publications, supports_plan
        from saia.controlled_collection import inventory_sha256

        configured_index = os.environ.get("SAIA_ARXIV_TRIGRAM_INDEX_DIR")
        if configured_index and supports_plan(search_plan):
            index_dir = Path(configured_index)
            index_manifest = json.loads((index_dir / "manifest.json").read_text())
            source = index_manifest.get("source") or {}
            if (source.get("full_inventory_sha256") != inventory_sha256(files, expected_rows, cfg)
                    or source.get("revision") != cfg["revision"]
                    or source.get("complete_pinned_inventory_indexed") is not True):
                raise ValueError("Configured arXiv index does not match the pinned mirror")
            try:
                return search_publications(index_dir, search_plan, limit)
            except ValueError as error:
                if str(error) not in {"Indexed exact cohort exceeds safe preview limit",
                                      "Indexed query intersects duplicate source IDs"}:
                    raise
    columns = ["id", "title", "abstract", "categories", "versions", "authors_parsed",
               "doi", "journal-ref", "comments", "update_date", "authors", "license"]
    scanned = coarse_matches = exact_matches = invalid_dates = 0
    selected: list[Publication] = []
    invalid_examples: list[dict] = []
    for path in files:
        parquet = pq.ParquetFile(path)
        available = set(parquet.schema_arrow.names)
        wanted_columns = [column for column in columns if column in available]
        for batch in parquet.iter_batches(batch_size=8192, columns=wanted_columns):
            scanned += batch.num_rows
            table = pa.Table.from_batches([batch])
            mask = _substring_mask(table, included)
            # Exclusions are intentionally not applied in the coarse filter:
            # substring("net") must not exclude the word "network".  The
            # authoritative word-boundary check below handles both sides.
            candidates = table.filter(pc.fill_null(mask, False)).to_pylist()
            coarse_matches += len(candidates)
            for row in candidates:
                if not matches_controlled_plan(row, search_plan):
                    continue
                exact_matches += 1
                try:
                    published = date.fromisoformat(first_submission(row.get("versions")))
                    if not start <= published < cutoff:
                        continue
                    identifier, record = to_record(
                        row, filename=path.name, revision=cfg["revision"])
                except ValueError as error:
                    invalid_dates += 1
                    if len(invalid_examples) < 10:
                        invalid_examples.append({"arxiv_id": row.get("id"), "reason": str(error)})
                    continue
                selected.append(Publication(
                    canonical_key=(f"doi:{_doi_key(str(record['arxiv_doi']))}"
                                   if record["arxiv_doi"] else f"title:{_title_key(record['title'])}"),
                    title=" ".join(str(record["title"]).split()),
                    abstract=" ".join(str(record["summary"]).split()) or None,
                    published_at=record["created"], sources=("arxiv",),
                    source_ids=(f"https://arxiv.org/abs/{identifier}",),
                    urls=(f"https://arxiv.org/abs/{identifier}",),
                    doi=_doi_key(str(record["arxiv_doi"])) or None,
                    authors=tuple(author["name"] for author in record["author"]),
                ))
    selected.sort(key=lambda work: (work.published_at, work.source_ids[0]), reverse=True)
    returned = tuple(selected[:limit])
    audit = {
        "adapter_version": cfg["version"], "dataset": cfg["dataset"],
        "revision": cfg["revision"], "inventory_files": len(files),
        "inventory_rows": expected_rows, "scanned_rows": scanned,
        "coarse_text_matches": coarse_matches, "exact_text_matches_all_dates": exact_matches,
        "eligible_matches": len(selected), "returned": len(returned),
        "invalid_selected_records": invalid_dates, "invalid_examples": invalid_examples,
        "date_semantics": "v1_created_utc; start_inclusive_as_of_exclusive",
        "text_semantics": {**cfg["matching"],
                           "matching_version": search_plan.get(
                               "matching_version", LITERAL_MATCHING_VERSION)},
        "limitations": cfg["limitations"],
        "coverage_comparable": None, "weak_signal_detection": False,
    }
    return LocalSearchResult(returned, audit)
