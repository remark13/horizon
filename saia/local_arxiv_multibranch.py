"""One-pass exact-phrase retrieval for an explicitly compiled branch plan."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import hashlib
import heapq
from collections import deque
from pathlib import Path

from saia.arxiv_metadata import first_submission, to_record
from saia.compiled_phrase_matching import matches_spec
from saia.discovery import Publication
from saia.local_arxiv_search import (policy, validate_inventory, _doi_key,
                                     _normalized_arrow, _title_key)
from saia.arxiv_metadata import (LITERAL_MATCHING_VERSION,
                                 ORTHOGRAPHIC_MATCHING_VERSION, normalized_phrase)


VERSION = "local-arxiv-multibranch-exact-0.4.51"


def scan(
    directory: str | Path,
    branch_specs: list[dict],
    date_from: date,
    as_of_date: date,
    limit_per_branch: int,
    *,
    selection_strategy: str = "latest",
    expected_files: int | None = None,
    expected_rows: int | None = None,
) -> dict:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if date_from >= as_of_date:
        raise ValueError("Начало периода должно быть раньше даты среза local arXiv.")
    if not 1 <= limit_per_branch <= 100:
        raise ValueError("Лимит local arXiv должен быть от 1 до 100 на ветвь.")
    if selection_strategy not in {"latest", "year_balanced_hash_v1"}:
        raise ValueError("Неизвестная стратегия отбора local arXiv.")
    ids = [spec.get("branch_id") for spec in branch_specs]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Нужны уникальные скомпилированные ветви local arXiv.")
    for spec in branch_specs:
        if not spec.get("included_phrases"):
            raise ValueError("Каждая ветвь local arXiv должна иметь точные включаемые фразы.")
    cfg = policy()
    expected_files = expected_files if expected_files is not None else cfg["expected_files"]
    expected_rows = expected_rows if expected_rows is not None else cfg["expected_rows"]
    files = validate_inventory(str(Path(directory).resolve()), expected_files, expected_rows)
    state = {branch_id: {
        "eligible_matches": 0, "exact_matches_all_dates": 0,
        "invalid_dates": 0, "year_counts": {}, "latest": [], "by_year": {},
    } for branch_id in ids}
    columns = [
        "id", "title", "abstract", "categories", "versions", "authors_parsed",
        "doi", "journal-ref", "comments", "update_date", "authors", "license",
    ]
    scanned = 0
    for path in files:
        parquet = pq.ParquetFile(path)
        wanted = [column for column in columns if column in parquet.schema_arrow.names]
        for batch in parquet.iter_batches(batch_size=8192, columns=wanted):
            scanned += batch.num_rows
            table = pa.Table.from_batches([batch])
            normalized = {field: _normalized_arrow(table[field])
                          for field in ("title", "abstract")}
            term_masks = {}
            for spec in branch_specs:
                literal_mask = None
                for phrase in spec["included_phrases"]:
                    needle = normalized_phrase(phrase, ORTHOGRAPHIC_MATCHING_VERSION)
                    if needle not in term_masks:
                        term_masks[needle] = pc.or_kleene(
                            pc.match_substring(normalized["title"], needle),
                            pc.match_substring(normalized["abstract"], needle),
                        )
                    literal_mask = (term_masks[needle] if literal_mask is None else
                                    pc.or_kleene(literal_mask, term_masks[needle]))
                compound_mask = None
                for group in spec.get("concept_groups") or []:
                    group_mask = None
                    for phrase in group:
                        needle = normalized_phrase(phrase, ORTHOGRAPHIC_MATCHING_VERSION)
                        if needle not in term_masks:
                            term_masks[needle] = pc.or_kleene(
                                pc.match_substring(normalized["title"], needle),
                                pc.match_substring(normalized["abstract"], needle),
                            )
                        group_mask = (term_masks[needle] if group_mask is None else
                                      pc.or_kleene(group_mask, term_masks[needle]))
                    compound_mask = (group_mask if compound_mask is None else
                                     pc.and_kleene(compound_mask, group_mask))
                mask = (pc.or_kleene(literal_mask, compound_mask)
                        if compound_mask is not None else literal_mask)
                rows = table.filter(pc.fill_null(mask, False)).to_pylist()
                branch = state[spec["branch_id"]]
                for row in rows:
                    if not matches_spec(row, spec):
                        continue
                    branch["exact_matches_all_dates"] += 1
                    try:
                        published = date.fromisoformat(first_submission(row.get("versions")))
                        if not date_from <= published < as_of_date:
                            continue
                        identifier, record = to_record(
                            row, filename=path.name, revision=cfg["revision"])
                    except ValueError:
                        branch["invalid_dates"] += 1
                        continue
                    branch["eligible_matches"] += 1
                    year = str(published.year)
                    branch["year_counts"][year] = branch["year_counts"].get(year, 0) + 1
                    publication = Publication(
                        canonical_key=(
                            f"doi:{_doi_key(str(record['arxiv_doi']))}"
                            if record["arxiv_doi"] else f"title:{_title_key(record['title'])}"
                        ),
                        title=" ".join(str(record["title"]).split()),
                        abstract=" ".join(str(record["summary"]).split()) or None,
                        published_at=record["created"], sources=("arxiv",),
                        source_ids=(f"https://arxiv.org/abs/{identifier}",),
                        urls=(f"https://arxiv.org/abs/{identifier}",),
                        doi=_doi_key(str(record["arxiv_doi"])) or None,
                        authors=tuple(author["name"] for author in record["author"]),
                    )
                    if selection_strategy == "latest":
                        item = (record["created"], identifier, asdict(publication))
                        if len(branch["latest"]) < limit_per_branch:
                            heapq.heappush(branch["latest"], item)
                        elif item[:2] > branch["latest"][0][:2]:
                            heapq.heapreplace(branch["latest"], item)
                    else:
                        # Stable bottom-k sample within each year. The hash is
                        # independent of shard order, and the per-year heaps
                        # keep memory bounded even for very broad phrases.
                        digest = int.from_bytes(
                            hashlib.sha256(identifier.encode()).digest()[:8], "big")
                        item = (-digest, identifier, asdict(publication))
                        heap = branch["by_year"].setdefault(year, [])
                        if len(heap) < limit_per_branch:
                            heapq.heappush(heap, item)
                        elif item[:2] > heap[0][:2]:
                            heapq.heapreplace(heap, item)
    results = []
    for spec in branch_specs:
        branch = state[spec["branch_id"]]
        if selection_strategy == "latest":
            works = [item[2] for item in sorted(branch.pop("latest"), reverse=True)]
            branch.pop("by_year")
        else:
            branch.pop("latest")
            years = sorted(branch["by_year"], reverse=True)
            queues = {
                year: deque(item[2] for item in sorted(
                    branch["by_year"][year], key=lambda item: (-item[0], item[1])))
                for year in years
            }
            branch.pop("by_year")
            works = []
            while len(works) < limit_per_branch and any(queues.values()):
                for year in years:
                    if queues[year] and len(works) < limit_per_branch:
                        works.append(queues[year].popleft())
        selected_year_counts = {}
        for work in works:
            year = work["published_at"][:4]
            selected_year_counts[year] = selected_year_counts.get(year, 0) + 1
        results.append({
            "branch_id": spec["branch_id"],
            "included_phrases": spec["included_phrases"],
            "concept_groups": spec.get("concept_groups") or [],
            "excluded_phrases": spec.get("excluded_phrases") or [],
            "matching_version": spec.get("matching_version", LITERAL_MATCHING_VERSION),
            **branch, "works": works, "selected_year_counts": selected_year_counts,
            "selection_strategy": selection_strategy,
        })
    return {
        "version": VERSION,
        "source": "local_arxiv_mirror",
        "revision": cfg["revision"],
        "inventory_files": len(files), "inventory_rows": expected_rows,
        "scanned_rows": scanned,
        "selection_strategy": selection_strategy,
        "date_from": date_from.isoformat(), "as_of_date": as_of_date.isoformat(),
        "branches": results,
        "coverage_comparable": None,
        "weak_signal_assessment_performed": False,
        "limitations": cfg["limitations"],
    }
