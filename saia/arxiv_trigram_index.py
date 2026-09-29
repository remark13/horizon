"""Reproducible FTS5 acceleration over pinned arXiv metadata.

The trigram index is a *coarse superset* for supported ASCII literal plans.
The approved Python word-boundary predicate is reapplied to every hit. Any
unsupported plan must use the existing complete mirror path.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from saia.arxiv_metadata import (LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION,
                                 first_submission, matches_controlled_plan, normalized_phrase)
from saia.controlled_collection import inventory_sha256, sha256_file
from saia.local_arxiv_search import policy, validate_inventory
from saia.priority_arxiv_full import MIN_FREE_BYTES


VERSION = "arxiv-trigram-index-v3"
GUARDED_VERSION = "arxiv-trigram-index-v4"
MAX_INDEX_BYTES = 24 * 1024 ** 3
_SAFE_TERM = re.compile(r"[a-z0-9][a-z0-9 .-]*\Z")


def supports_plan(plan: dict) -> bool:
    """Conservatively accept only plans with a safe trigram superset."""
    included = plan.get("included_terms") or []
    version = plan.get("matching_version", LITERAL_MATCHING_VERSION)
    if version not in {LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION}:
        return False
    if not isinstance(included, list) or not included:
        return False
    for term in included:
        if not isinstance(term, str):
            return False
        normalized = normalized_phrase(term, version)
        if not _SAFE_TERM.fullmatch(normalized):
            return False
        if version == LITERAL_MATCHING_VERSION and len(normalized) < 3:
            return False
        if version == ORTHOGRAPHIC_MATCHING_VERSION and not any(
                len(token) >= 3 for token in normalized.split()):
            return False
    return True


def _coarse_grams(term: str, matching_version: str) -> list[str]:
    phrase = normalized_phrase(term, matching_version)
    if matching_version == ORTHOGRAPHIC_MATCHING_VERSION:
        # Cross-word trigrams would miss source hyphens/slashes. Each intact
        # token's trigrams must still occur in a true orthographic match.
        return sorted({token[index:index + 3]
                       for token in phrase.split() if len(token) >= 3
                       for index in range(len(token) - 2)})
    return sorted({phrase[index:index + 3] for index in range(len(phrase) - 2)})


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA cache_size=-131072")
    connection.executescript("""
        CREATE TABLE works (
            arxiv_id TEXT NOT NULL UNIQUE,
            title TEXT, abstract TEXT, first_submission_date TEXT NOT NULL,
            authors TEXT, authors_parsed_json TEXT NOT NULL,
            versions_json TEXT NOT NULL,
            doi TEXT, categories TEXT, license TEXT, journal_ref TEXT, comments TEXT,
            snapshot_update_date TEXT, source_shard TEXT NOT NULL
        );
        CREATE INDEX works_first_date_idx ON works(first_submission_date);
        CREATE VIRTUAL TABLE work_fts USING fts5(
            text, content='', tokenize='trigram', detail='none', columnsize=0
        );
    """)


def build(*, mirror_dir: Path, output_dir: Path, max_shards: int | None = None) -> dict:
    import pyarrow.parquet as pq

    if output_dir.exists():
        raise FileExistsError("arXiv index output is immutable")
    if shutil.disk_usage(output_dir.parent).free < MIN_FREE_BYTES:
        raise OSError("Insufficient free disk reserve")
    cfg = policy()
    all_files = validate_inventory(str(mirror_dir.resolve()), cfg["expected_files"], cfg["expected_rows"])
    if max_shards is not None and not 1 <= max_shards <= len(all_files):
        raise ValueError("Invalid shard limit")
    files = all_files[:max_shards] if max_shards is not None else all_files
    inventory_hash = inventory_sha256(all_files, cfg["expected_rows"], cfg)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output_dir.name + ".tmp-", dir=output_dir.parent) as temp_name:
        temp = Path(temp_name)
        db_path = temp / "index.sqlite3"
        connection = sqlite3.connect(db_path)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=OFF")
        _create_schema(connection)
        scanned = invalid_dates = duplicates = 0
        try:
            for shard in files:
                parquet = pq.ParquetFile(shard)
                for batch in parquet.iter_batches(batch_size=8192, columns=[
                    "id", "title", "abstract", "versions", "authors",
                    "authors_parsed", "doi", "categories", "license",
                    "journal-ref", "comments", "update_date",
                ]):
                    scanned += batch.num_rows
                    rows = []
                    for raw in batch.to_pylist():
                        try:
                            first_date = first_submission(raw.get("versions"))
                            date.fromisoformat(first_date)
                        except (TypeError, ValueError, IndexError):
                            invalid_dates += 1
                            continue
                        update = raw.get("update_date")
                        rows.append((str(raw.get("id") or ""), raw.get("title"), raw.get("abstract"),
                                     first_date,
                                     raw.get("authors"),
                                     json.dumps(raw.get("authors_parsed") or [], ensure_ascii=False),
                                     json.dumps(raw.get("versions") or [], ensure_ascii=False),
                                     raw.get("doi"), raw.get("categories"),
                                     raw.get("license"), raw.get("journal-ref"),
                                     raw.get("comments"), update.isoformat() if update else None,
                                     shard.name))
                    before = connection.total_changes
                    connection.executemany("""INSERT OR IGNORE INTO works
                        (arxiv_id,title,abstract,first_submission_date,
                         authors,authors_parsed_json,versions_json,doi,categories,license,
                         journal_ref,comments,snapshot_update_date,source_shard)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
                    duplicates += len(rows) - (connection.total_changes - before)
                connection.commit()
                if (db_path.stat().st_size > MAX_INDEX_BYTES
                        or shutil.disk_usage(temp).free < MIN_FREE_BYTES):
                    raise OSError("Index exceeded disk cap or reserve")
            cursor = connection.execute("SELECT rowid,title,abstract FROM works ORDER BY rowid")
            fts_indexed = 0
            while batch := cursor.fetchmany(8192):
                connection.executemany("INSERT INTO work_fts(rowid,text) VALUES (?,?)", [
                    (rowid, normalized_phrase(title) + " " + normalized_phrase(abstract))
                    for rowid, title, abstract in batch
                ])
                fts_indexed += len(batch)
            connection.commit()
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            indexed = connection.execute("SELECT count(*) FROM works").fetchone()[0]
            if indexed != fts_indexed or scanned != sum(pq.ParquetFile(path).metadata.num_rows
                                                       for path in files):
                raise ValueError("Index/source count mismatch")
        finally:
            connection.close()
        size = db_path.stat().st_size
        if size > MAX_INDEX_BYTES or shutil.disk_usage(temp).free < MIN_FREE_BYTES:
            raise OSError("Final index exceeded disk cap or reserve")
        manifest = {
            "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {"dataset": cfg["dataset"], "revision": cfg["revision"],
                       "full_inventory_sha256": inventory_hash,
                       "full_inventory_rows": cfg["expected_rows"],
                       "indexed_shards": [path.name for path in files],
                       "complete_pinned_inventory_indexed": len(files) == len(all_files)},
            "counts": {"scanned_rows": scanned, "indexed_unique_arxiv_ids": indexed,
                       "invalid_v1_dates_excluded": invalid_dates,
                       "duplicate_id_rows_excluded_first_snapshot_row_wins": duplicates},
            "file": {"name": db_path.name, "bytes": size, "sha256": sha256_file(db_path)},
            "search_policy": {"index_is_coarse_prefilter_only": True,
                              "approved_word_boundary_predicate_reapplied": True,
                              "unsupported_or_uncovered_plans_use_full_mirror": True,
                              "exact_text_is_current_snapshot_not_historical_v1": True},
            "rights": {"metadata": "arXiv CC0; https://info.arxiv.org/help/license/index.html",
                       "full_text_pdfs_included": False},
            "limits": {"max_index_bytes": MAX_INDEX_BYTES,
                       "min_free_disk_reserve_bytes": MIN_FREE_BYTES,
                       "weak_signal_detection": False,
                       "field_denominator": "all_pinned_arxiv_only_not_world_science"},
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temp, output_dir)
    return manifest


def promote_with_duplicate_guard(*, base_index_dir: Path, mirror_dir: Path,
                                 duplicate_audit_path: Path, output_dir: Path) -> dict:
    """Hardlink v3 index and add a small exact guard for duplicate source rows.

    A matching duplicate makes the caller use the original full scan. This
    preserves retrieval semantics without copying another 8 GiB index.
    """
    import pyarrow.parquet as pq

    if output_dir.exists():
        raise FileExistsError("Guarded arXiv index output is immutable")
    base = json.loads((base_index_dir / "manifest.json").read_text(encoding="utf-8"))
    if (base.get("version") != VERSION
            or base["source"].get("complete_pinned_inventory_indexed") is not True
            or base["counts"].get("invalid_v1_dates_excluded") != 0):
        raise ValueError("Only a complete v3 index with valid v1 dates may be promoted")
    base_file = base_index_dir / base["file"]["name"]
    if sha256_file(base_file) != base["file"]["sha256"]:
        raise ValueError("Base index hash mismatch")
    audit = json.loads(duplicate_audit_path.read_text(encoding="utf-8"))
    if (audit.get("index_manifest_sha256") != sha256_file(base_index_dir / "manifest.json")
            or audit.get("duplicate_rows") != base["counts"][
                "duplicate_id_rows_excluded_first_snapshot_row_wins"]):
        raise ValueError("Duplicate audit does not match the base index")
    duplicate_ids = {item["arxiv_id"] for item in audit.get("duplicates") or []}
    if len(audit.get("duplicates") or []) != audit["duplicate_rows"]:
        raise ValueError("Incomplete duplicate audit")
    cfg = policy()
    files = validate_inventory(str(mirror_dir.resolve()), cfg["expected_files"], cfg["expected_rows"])
    inventory_hash = inventory_sha256(files, cfg["expected_rows"], cfg)
    if inventory_hash != base["source"]["full_inventory_sha256"]:
        raise ValueError("Duplicate guard mirror does not match the base index")
    variants: list[dict] = []
    seen: dict[str, int] = {}
    for shard in files:
        parquet = pq.ParquetFile(shard)
        for batch in parquet.iter_batches(batch_size=8192,
                                          columns=["id", "title", "abstract", "versions"]):
            for row in batch.to_pylist():
                identifier = row.get("id")
                if identifier not in duplicate_ids:
                    continue
                variants.append({"arxiv_id": identifier, "title": row.get("title"),
                                 "abstract": row.get("abstract"),
                                 "first_submission_date": first_submission(row.get("versions")),
                                 "source_shard": shard.name})
                seen[identifier] = seen.get(identifier, 0) + 1
    if (set(seen) != duplicate_ids or any(count < 2 for count in seen.values())
            or sum(count - 1 for count in seen.values()) != audit["duplicate_rows"]):
        raise ValueError("Duplicate guard does not cover every duplicate source row")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output_dir.name + ".tmp-", dir=output_dir.parent) as name:
        temp = Path(name)
        guard_path = temp / "duplicate_guard.json"
        guard = {"version": "arxiv-duplicate-text-guard-v1",
                 "source_inventory_sha256": inventory_hash,
                 "base_index_manifest_sha256": sha256_file(base_index_dir / "manifest.json"),
                 "duplicate_arxiv_ids": len(duplicate_ids),
                 "duplicate_source_rows": len(variants), "variants": variants}
        guard_path.write_text(json.dumps(guard, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")) + "\n", encoding="utf-8")
        os.link(base_file, temp / base["file"]["name"])
        manifest = dict(base)
        manifest["version"] = GUARDED_VERSION
        manifest["created_at"] = datetime.now(timezone.utc).isoformat()
        manifest["base_index_manifest_sha256"] = guard["base_index_manifest_sha256"]
        manifest["duplicate_guard"] = {"name": guard_path.name,
                                       "bytes": guard_path.stat().st_size,
                                       "sha256": sha256_file(guard_path),
                                       "duplicate_arxiv_ids": len(duplicate_ids),
                                       "matching_duplicate_query_uses_full_mirror": True}
        manifest["search_policy"] = {**base["search_policy"],
                                     "duplicate_source_rows_guarded": True}
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temp, output_dir)
    return manifest


def _duplicate_guard_intersects(index_dir: Path, manifest: dict,
                                plan: dict, start: date, cutoff: date) -> bool:
    info = manifest["duplicate_guard"]
    guard_path = index_dir / info["name"]
    if (guard_path.stat().st_size != info["bytes"]
            or sha256_file(guard_path) != info["sha256"]):
        raise ValueError("Duplicate guard file differs from manifest")
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    if (guard.get("source_inventory_sha256") != manifest["source"]["full_inventory_sha256"]
            or guard.get("base_index_manifest_sha256") != manifest["base_index_manifest_sha256"]
            or len(guard.get("variants") or []) != guard.get("duplicate_source_rows")):
        raise ValueError("Duplicate guard provenance mismatch")
    return any(start <= date.fromisoformat(item["first_submission_date"]) < cutoff
               and matches_controlled_plan(item, plan) for item in guard["variants"])


def exact_search(index_dir: Path, plan: dict, *, allow_partial_diagnostic: bool = False) -> dict:
    """Return all exact IDs and an audit; never silently use a partial index."""
    if not supports_plan(plan):
        raise ValueError("Index does not safely cover this matching plan")
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") not in {VERSION, GUARDED_VERSION}:
        raise ValueError("Unknown index version")
    if (index_dir / "index.sqlite3").stat().st_size != manifest["file"]["bytes"]:
        raise ValueError("Index file size differs from immutable manifest")
    if not manifest["source"]["complete_pinned_inventory_indexed"] and not allow_partial_diagnostic:
        raise ValueError("Partial shard index cannot answer a complete-corpus query")
    start = date.fromisoformat(plan["date_from"])
    cutoff = date.fromisoformat(plan["as_of_date"])
    if start >= cutoff:
        raise ValueError("Empty search period")
    if (manifest["version"] == GUARDED_VERSION
            and _duplicate_guard_intersects(index_dir, manifest, plan, start, cutoff)):
        raise ValueError("Indexed query intersects duplicate source IDs")
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    ids: set[str] = set()
    coarse = 0
    try:
        for term in plan["included_terms"]:
            # A contiguous phrase necessarily contains each of its trigrams;
            # AND of these tokens is a superset, never a final match decision.
            grams = _coarse_grams(term, plan.get("matching_version", LITERAL_MATCHING_VERSION))
            fts_query = " AND ".join('"' + gram + '"' for gram in grams)
            cursor = connection.execute("""SELECT w.arxiv_id,w.title,w.abstract
                FROM work_fts JOIN works AS w ON w.rowid=work_fts.rowid
                WHERE work_fts MATCH ?
                AND w.first_submission_date >= ? AND w.first_submission_date < ?""",
                (fts_query, start.isoformat(), cutoff.isoformat()))
            for row in cursor:
                coarse += 1
                if matches_controlled_plan({"title": row["title"], "abstract": row["abstract"]}, plan):
                    ids.add(row["arxiv_id"])
    finally:
        connection.close()
    return {"arxiv_ids": sorted(ids), "audit": {
        "index_version": manifest["version"],
        "duplicate_source_rows_guarded": manifest["version"] == GUARDED_VERSION,
        "complete_pinned_inventory_indexed": manifest["source"][
            "complete_pinned_inventory_indexed"],
        "coarse_hits_with_possible_or_repetition": coarse, "exact_unique_ids": len(ids),
        "weak_signal_detection": False}}


def search_publications(index_dir: Path, plan: dict, limit: int = 100,
                        *, max_eligible_records: int = 20_000,
                        allow_partial_diagnostic: bool = False):
    """Preview-compatible publications; broad scopes must use the old path."""
    from saia.arxiv_metadata import to_record
    from saia.discovery import Publication
    from saia.local_arxiv_search import LocalSearchResult, _doi_key, _title_key

    if not 1 <= limit <= 100 or max_eligible_records < limit:
        raise ValueError("Invalid indexed search limit")
    matches = exact_search(index_dir, plan,
                           allow_partial_diagnostic=allow_partial_diagnostic)
    identifiers = matches["arxiv_ids"]
    if len(identifiers) > max_eligible_records:
        raise ValueError("Indexed exact cohort exceeds safe preview limit")
    cfg = policy()
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    selected = []
    invalid_examples = []
    invalid = 0
    try:
        for offset in range(0, len(identifiers), 500):
            chunk = identifiers[offset:offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(f"SELECT * FROM works WHERE arxiv_id IN ({placeholders})", chunk)
            for item in rows:
                identifier = item["arxiv_id"]
                raw = {"id": identifier, "title": item["title"], "abstract": item["abstract"],
                       "categories": item["categories"],
                       "versions": json.loads(item["versions_json"]),
                       "authors_parsed": json.loads(item["authors_parsed_json"]),
                       "authors": item["authors"], "doi": item["doi"],
                       "journal-ref": item["journal_ref"], "comments": item["comments"],
                       "update_date": item["snapshot_update_date"],
                       "license": item["license"]}
                try:
                    _, record = to_record(raw, filename=item["source_shard"],
                                          revision=cfg["revision"])
                except ValueError as error:
                    invalid += 1
                    if len(invalid_examples) < 10:
                        invalid_examples.append({"arxiv_id": identifier, "reason": str(error)})
                    continue
                url = f"https://arxiv.org/abs/{identifier}"
                selected.append(Publication(
                    canonical_key=(f"doi:{_doi_key(str(record['arxiv_doi']))}"
                                   if record["arxiv_doi"] else f"title:{_title_key(record['title'])}"),
                    title=" ".join(str(record["title"]).split()),
                    abstract=" ".join(str(record["summary"]).split()) or None,
                    published_at=record["created"], sources=("arxiv",),
                    source_ids=(url,), urls=(url,),
                    doi=_doi_key(str(record["arxiv_doi"])) or None,
                    authors=tuple(author["name"] for author in record["author"]),
                ))
    finally:
        connection.close()
    if len(selected) + invalid != len(identifiers):
        raise ValueError("Indexed match IDs could not all be materialized")
    selected.sort(key=lambda work: (work.published_at, work.source_ids[0]), reverse=True)
    audit = {"adapter_version": matches["audit"]["index_version"], "dataset": cfg["dataset"],
             "revision": cfg["revision"], "inventory_files": cfg["expected_files"],
             "inventory_rows": cfg["expected_rows"],
             "scanned_rows": 0, "indexed_prefilter": matches["audit"],
             "eligible_matches": len(selected), "returned": min(limit, len(selected)),
             "invalid_selected_records": invalid, "invalid_examples": invalid_examples,
             "date_semantics": "v1_created_utc; start_inclusive_as_of_exclusive",
             "text_semantics": {"matching_version": plan.get(
                 "matching_version", LITERAL_MATCHING_VERSION),
                                "approved_predicate_reapplied": True},
             "limitations": cfg["limitations"],
             "coverage_comparable": None, "weak_signal_detection": False}
    return LocalSearchResult(tuple(selected[:limit]), audit)
