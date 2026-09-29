"""Additive AND-of-concepts/OR-of-synonyms arXiv retrieval on a pinned index.

Legacy included_terms retains its OR semantics. This module does not approve
query plans or turn literal retrieval into a weak-signal judgement.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3

from saia.arxiv_metadata import (LITERAL_MATCHING_VERSION,
                                 ORTHOGRAPHIC_MATCHING_VERSION, _matches_phrase)
from saia.arxiv_trigram_index import GUARDED_VERSION, _coarse_grams, supports_plan
from saia.controlled_collection import sha256_file


POLICY_VERSION = "priority-concept-groups-v1"


def supports_concept_plan(plan: dict) -> bool:
    groups = plan.get("concept_groups")
    if (not isinstance(groups, list) or not 2 <= len(groups) <= 5
            or any(not isinstance(group, list) or not 1 <= len(group) <= 3
                   for group in groups)):
        return False
    version = plan.get("matching_version", LITERAL_MATCHING_VERSION)
    if version not in {LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION}:
        return False
    for group in groups:
        for phrase in group:
            if not supports_plan({"included_terms": [phrase], "matching_version": version}):
                return False
    exclusions = plan.get("exclusions", [])
    if (not isinstance(exclusions, list) or len(exclusions) > 10
            or any(not isinstance(term, str) or not term.strip()
                   for term in exclusions)):
        return False
    return True


def _matches(row: dict, plan: dict) -> bool:
    texts = [str(row[field] or "") for field in ("title", "abstract")]
    version = plan.get("matching_version", LITERAL_MATCHING_VERSION)
    return (all(any(_matches_phrase(text, phrase, version)
                    for phrase in group for text in texts)
                for group in plan["concept_groups"])
            and not any(_matches_phrase(text, phrase, version)
                        for phrase in plan.get("exclusions", []) for text in texts))


def _fts_query(plan: dict) -> str:
    version = plan.get("matching_version", LITERAL_MATCHING_VERSION)
    group_queries = []
    for group in plan["concept_groups"]:
        alternatives = []
        for phrase in group:
            grams = _coarse_grams(phrase, version)
            alternatives.append("(" + " AND ".join('"' + gram + '"' for gram in grams) + ")")
        group_queries.append("(" + " OR ".join(alternatives) + ")")
    return " AND ".join(group_queries)


def _guard_intersects(index_dir: Path, manifest: dict, plan: dict,
                      start: date, cutoff: date) -> bool:
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
    return any(start <= date.fromisoformat(row["first_submission_date"]) < cutoff
               and _matches(row, plan) for row in guard["variants"])


def exact_concept_search(index_dir: Path, plan: dict,
                         *, max_matches: int = 20_000) -> dict:
    if not supports_concept_plan(plan):
        raise ValueError("Unsupported concept-group plan")
    if max_matches < 1:
        raise ValueError("Invalid match limit")
    start, cutoff = date.fromisoformat(plan["date_from"]), date.fromisoformat(plan["as_of_date"])
    if start >= cutoff:
        raise ValueError("Empty concept-group period")
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("version") != GUARDED_VERSION
            or not manifest["source"]["complete_pinned_inventory_indexed"]
            or (index_dir / "index.sqlite3").stat().st_size != manifest["file"]["bytes"]):
        raise ValueError("Only the complete duplicate-guarded pinned index is accepted")
    if _guard_intersects(index_dir, manifest, plan, start, cutoff):
        raise ValueError("Concept query intersects duplicate source IDs")
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                                 uri=True)
    connection.row_factory = sqlite3.Row
    ids = set()
    coarse = 0
    try:
        cursor = connection.execute("""SELECT w.arxiv_id,w.title,w.abstract
            FROM work_fts JOIN works AS w ON w.rowid=work_fts.rowid
            WHERE work_fts MATCH ?
            AND w.first_submission_date >= ? AND w.first_submission_date < ?""",
            (_fts_query(plan), start.isoformat(), cutoff.isoformat()))
        for row in cursor:
            coarse += 1
            if _matches(row, plan):
                ids.add(row["arxiv_id"])
                if len(ids) > max_matches:
                    raise ValueError("Concept cohort exceeds safe limit; refine the plan")
    finally:
        connection.close()
    return {"arxiv_ids": sorted(ids), "audit": {
        "policy_version": POLICY_VERSION, "index_version": manifest["version"],
        "coarse_candidates": coarse, "exact_unique_ids": len(ids),
        "complete_pinned_inventory_indexed": True,
        "weak_signal_detection": False,
        "legacy_or_semantics_unchanged": True}}
