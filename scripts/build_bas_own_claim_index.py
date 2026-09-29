"""Build a bounded, provenance-preserving FTS index of arXiv own-work cues.

The index retrieves possible contribution sentences; it does NOT verify
scientific primacy, topical relevance, line coherence or emergence.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

from saia.broad_title_phrases import collect_parent
from saia.controlled_collection import sha256_file
from scripts.probe_bas_article_facets_v2 import own_claim_ids
from scripts.probe_grounded_task_facets_v3 import source_spans


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-own-claim-index-pilot-v1"
REVIEW_WORDS = ("survey", "review", "overview", "bibliometric", "meta-analysis")


def extract(row: dict) -> tuple[list[dict], str]:
    title = " ".join((row.get("title") or "").split())
    abstract = " ".join((row.get("abstract") or "").split())
    if not title or not abstract:
        return [], "missing_title_or_abstract"
    spans = source_spans({"title": title, "abstract": abstract})
    review = any(word in title.casefold() for word in REVIEW_WORDS)
    claims = []
    for span_id in own_claim_ids(spans):
        sentence = spans[span_id]
        claims.append({"span_id": span_id, "text": sentence,
                       "text_sha256": hashlib.sha256(sentence.encode("utf-8")).hexdigest(),
                       "format_hint": "review_or_synthesis" if review else "unclassified"})
    return claims, "claim_cues_found" if claims else "no_explicit_claim_cue"


def build(rows: list[dict], db_path: Path, *, max_claims: int) -> dict:
    if not rows or len({row["arxiv_id"] for row in rows}) != len(rows):
        raise ValueError("Parent works missing or duplicated")
    if db_path.exists():
        raise FileExistsError(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    reasons = Counter()
    claim_count = 0
    try:
        connection.executescript("""
            CREATE TABLE works (
                arxiv_id TEXT PRIMARY KEY, first_submission_date TEXT NOT NULL,
                title TEXT NOT NULL, extraction_reason TEXT NOT NULL
            );
            CREATE TABLE claims (
                claim_id TEXT PRIMARY KEY, arxiv_id TEXT NOT NULL,
                span_id TEXT NOT NULL, text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL, format_hint TEXT NOT NULL,
                FOREIGN KEY (arxiv_id) REFERENCES works(arxiv_id)
            );
            CREATE VIRTUAL TABLE claim_fts USING fts5(
                text, claim_id UNINDEXED, tokenize='unicode61'
            );
            CREATE INDEX claims_work_idx ON claims(arxiv_id);
        """)
        for row in sorted(rows, key=lambda item: item["arxiv_id"]):
            claims, reason = extract(row)
            reasons[reason] += 1
            title = " ".join((row.get("title") or "").split())
            connection.execute("INSERT INTO works VALUES (?,?,?,?)",
                               (row["arxiv_id"], row["first_submission_date"],
                                title, reason))
            for claim in claims:
                claim_count += 1
                if claim_count > max_claims:
                    raise ValueError("Claim sentence cap exceeded")
                claim_id = row["arxiv_id"] + ":" + claim["span_id"]
                connection.execute("INSERT INTO claims VALUES (?,?,?,?,?,?)",
                                   (claim_id, row["arxiv_id"], claim["span_id"],
                                    claim["text"], claim["text_sha256"],
                                    claim["format_hint"]))
                connection.execute("INSERT INTO claim_fts (text,claim_id) VALUES (?,?)",
                                   (claim["text"], claim_id))
        connection.commit()
        check = connection.execute("SELECT count(*) FROM works").fetchone()[0]
        if check != len(rows):
            raise ValueError("Work count reconciliation failed")
        if connection.execute("SELECT count(*) FROM claims").fetchone()[0] != claim_count:
            raise ValueError("Claim count reconciliation failed")
    finally:
        connection.close()
    return {"parent_works": len(rows), "claim_sentences": claim_count,
            "work_extraction_reasons": dict(reasons)}


def search(db_path: Path, phrase: str, *, limit: int = 100) -> list[dict]:
    if not phrase or not 1 <= limit <= 1000:
        raise ValueError("Invalid bounded search")
    # Quoted FTS phrase is a coarse retrieval step, never a truth judgement.
    query = '"' + phrase.replace('"', '""') + '"'
    connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("""
            SELECT c.claim_id,c.arxiv_id,c.span_id,c.text,c.format_hint,
                   w.first_submission_date,w.title
            FROM claim_fts AS f JOIN claims AS c ON c.claim_id=f.claim_id
            JOIN works AS w ON w.arxiv_id=c.arxiv_id
            WHERE claim_fts MATCH ? ORDER BY w.first_submission_date,c.claim_id
            LIMIT ?
        """, (query, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if cfg.get("version") != VERSION or cfg.get("production_use") is not False:
        raise ValueError("Invalid diagnostic protocol")
    plan_path = (ROOT / cfg["parent_plan"]).resolve()
    index_dir = (ROOT / cfg["index"]).resolve()
    if (not plan_path.is_relative_to(ROOT) or not index_dir.is_relative_to(ROOT)
            or sha256_file(plan_path) != cfg["parent_plan_sha256"]
            or sha256_file(index_dir / "manifest.json") != cfg["index_manifest_sha256"]):
        raise ValueError("Frozen parent or source index changed")
    if shutil.disk_usage(args.output_dir.parent).free < cfg["min_free_disk_reserve_bytes"]:
        raise ValueError("Free disk reserve would be violated")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rows, parent_audit = collect_parent(index_dir, plan)
    if len(rows) > cfg["max_parent_works"]:
        raise ValueError("Parent work cap exceeded")
    db_path = args.output_dir / "claims.sqlite3"
    counts = build(rows, db_path, max_claims=cfg["max_claim_sentences"])
    if db_path.stat().st_size > cfg["max_output_bytes"]:
        raise ValueError("Claim index exceeded disk cap")
    source_manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "config_sha256": sha256_file(args.config),
                "parent_plan_sha256": cfg["parent_plan_sha256"],
                "source_index_manifest_sha256": cfg["index_manifest_sha256"],
                "source_revision": source_manifest["source"]["revision"],
                "source_rights": source_manifest["rights"],
                "period": {"from": plan["date_from"],
                           "to_exclusive": plan["as_of_date_exclusive"]},
                "parent_collection_audit": parent_audit, **counts,
                "file": {"name": db_path.name, "bytes": db_path.stat().st_size,
                         "sha256": sha256_file(db_path)},
                "max_output_bytes": cfg["max_output_bytes"],
                "min_free_disk_reserve_bytes": cfg["min_free_disk_reserve_bytes"],
                "limitations": cfg["limitations"],
                "production_changed": False,
                "weak_signal_accuracy_measured": False}
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({"parent_works": counts["parent_works"],
                      "claim_sentences": counts["claim_sentences"],
                      "index_bytes": db_path.stat().st_size}), flush=True)


if __name__ == "__main__":
    main()
