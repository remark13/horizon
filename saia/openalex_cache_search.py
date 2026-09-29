"""Bounded, year-spread reuse of OpenAlex records already ingested by SAIA.

This cache is opportunistic: it is not the OpenAlex snapshot, a complete field
corpus, or evidence that different years had comparable discovery coverage.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict
from datetime import date
import hashlib
import heapq
import re

from saia import db
from saia.compiled_phrase_matching import matches_spec
from saia.discovery import Publication


VERSION = "openalex-ingested-cache-year-spread-0.4.52"


def _title_key(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).strip()


def _select_year_spread(heaps: dict[str, list[tuple]], limit: int) -> list[tuple]:
    years = sorted(heaps, reverse=True)
    queues = {
        year: deque(item[2] for item in sorted(
            heaps[year], key=lambda item: (-item[0], item[1])))
        for year in years
    }
    chosen = []
    while len(chosen) < limit and any(queues.values()):
        for year in years:
            if queues[year] and len(chosen) < limit:
                chosen.append(queues[year].popleft())
    return chosen


def scan(branch_specs: list[dict], date_from: date, as_of_date: date,
         limit_per_branch: int, *, rows: list[tuple] | None = None) -> dict:
    """Search the *existing* OpenAlex-ID cache with the compiled exact phrases.

    ``rows`` is only for deterministic tests. Production reads immutable facts
    from prior SAIA ingestions, then freezes selected records in the job result.
    """
    if date_from >= as_of_date or not 1 <= limit_per_branch <= 100:
        raise ValueError("Некорректный период или лимит кэша OpenAlex.")
    ids = [spec.get("branch_id") for spec in branch_specs]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Нужны уникальные ветви кэша OpenAlex.")
    database_rows = rows is None
    if database_rows:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (i.value) w.work_id,i.value,w.canonical_title,"
                "w.abstract,w.effective_date,"
                "(SELECT value FROM identifier WHERE work_id=w.work_id AND kind='doi' LIMIT 1),"
                "w.created_at FROM work w JOIN identifier i USING(work_id) "
                "WHERE i.kind='openalex' AND w.effective_date >= %s "
                "AND w.effective_date < %s "
                "ORDER BY i.value,w.created_at DESC,w.work_id DESC",
                (date_from, as_of_date),
            )
            rows = cur.fetchall()
    state = {branch_id: {"eligible_matches": 0, "year_counts": {}, "heaps": {}}
             for branch_id in ids}
    latest_cache_ingest = max((row[6] for row in rows if row[6]), default=None)
    for row in rows:
        work_id, identifier, title, abstract, published, doi, _created = row
        if not identifier or not title or not date_from <= published < as_of_date:
            continue
        text = {"title": title, "abstract": abstract or ""}
        for spec in branch_specs:
            if not matches_spec(text, spec):
                continue
            branch = state[spec["branch_id"]]
            branch["eligible_matches"] += 1
            year = str(published.year)
            branch["year_counts"][year] = branch["year_counts"].get(year, 0) + 1
            score = int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:8], "big")
            item = (-score, identifier, row)
            heap = branch["heaps"].setdefault(year, [])
            if len(heap) < limit_per_branch:
                heapq.heappush(heap, item)
            elif item[:2] > heap[0][:2]:
                heapq.heapreplace(heap, item)
    selected = {
        spec["branch_id"]: _select_year_spread(
            state[spec["branch_id"]]["heaps"], limit_per_branch)
        for spec in branch_specs
    }
    authors_by_work: dict[int, tuple[str, ...]] = {}
    if database_rows:
        selected_ids = sorted({row[0] for chosen in selected.values() for row in chosen})
        if selected_ids:
            with db.connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT wa.work_id,a.display_name FROM work_author wa "
                    "JOIN author a USING(author_id) WHERE wa.work_id = ANY(%s) "
                    "ORDER BY wa.work_id,wa.author_position,a.display_name",
                    (selected_ids,),
                )
                names: dict[int, list[str]] = {}
                for work_id, name in cur.fetchall():
                    if name and name not in names.setdefault(work_id, []):
                        names[work_id].append(name)
                authors_by_work = {key: tuple(value) for key, value in names.items()}
    results = []
    for spec in branch_specs:
        branch = state[spec["branch_id"]]
        chosen = selected[spec["branch_id"]]
        branch.pop("heaps")
        selected_year_counts = {}
        works = []
        for _work_id, identifier, title, abstract, published, doi, _created in chosen:
            year = str(published.year)
            selected_year_counts[year] = selected_year_counts.get(year, 0) + 1
            doi = str(doi or "").removeprefix("https://doi.org/").removeprefix("doi:").strip() or None
            publication = Publication(
                canonical_key=f"doi:{doi.casefold()}" if doi else f"title:{_title_key(title)}",
                title=" ".join(title.split()), abstract=" ".join((abstract or "").split()) or None,
                published_at=published.isoformat(), sources=("openalex",),
                source_ids=(f"https://openalex.org/{identifier}",),
                urls=(f"https://openalex.org/{identifier}",), doi=doi,
                authors=authors_by_work.get(_work_id, ()),
            )
            works.append(asdict(publication))
        results.append({
            "branch_id": spec["branch_id"], "eligible_matches": branch["eligible_matches"],
            "year_counts": dict(sorted(branch["year_counts"].items())),
            "selected_year_counts": dict(sorted(selected_year_counts.items())),
            "works": works,
        })
    return {
        "version": VERSION, "selection_strategy": "year_balanced_hash_v1",
        "source": "previously_ingested_openalex_identifiers",
        "available_unique_ids_in_period": len(rows),
        "latest_cache_ingested_at": latest_cache_ingest.isoformat() if latest_cache_ingest else None,
        "date_from": date_from.isoformat(), "as_of_date": as_of_date.isoformat(),
        "branches": results, "coverage_comparable": None,
        "limitations": [
            "Кэш состоит из ранее загруженных Horizon работ OpenAlex, а не полного снимка OpenAlex.",
            "Распределение кэша по годам зависит от прежних запросов; рост по нему не оценивается.",
            "Авторы восстановлены из ранее загруженных метаданных, аффилиации не восстановлены.",
        ],
    }
