"""Propose BAS technology fragments from explicit own-work sentences.

This is a contribution-first lexical diagnostic, not signal classification.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3

from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-contribution-phrase-proposals-v1"
WORDS = re.compile(r"[a-z]+(?:[0-9]+)?")


def sentence_phrases(text: str, *, stop: set[str], generic: set[str],
                     lengths: list[int]) -> set[str]:
    tokens = WORDS.findall(text.casefold())
    phrases = set()
    for size in lengths:
        for offset in range(len(tokens) - size + 1):
            part = tokens[offset:offset + size]
            if (any(len(word) < 3 or word in stop for word in part)
                    or all(word in generic for word in part)):
                continue
            phrases.add(" ".join(part))
    return phrases


def propose(works: dict[str, str], claims: list[dict], cfg: dict) -> dict:
    if cfg.get("version") != VERSION or cfg.get("production_use") is not False:
        raise ValueError("Invalid contribution phrase protocol")
    start, end = cfg["date_from"], cfg["as_of_date_exclusive"]
    prior, recent = cfg["prior_from"], cfg["recent_from"]
    if not start < prior < recent < end:
        raise ValueError("Invalid complete period windows")
    parent = {identifier: day for identifier, day in works.items()
              if start <= day < end}
    if len(parent) != len(works):
        raise ValueError("Claim parent outside frozen period")
    recent_parent = sum(day >= recent for day in parent.values())
    prior_parent = sum(prior <= day < recent for day in parent.values())
    if not recent_parent or not prior_parent:
        raise ValueError("Missing parent comparison windows")
    stop = set(cfg["extra_stopwords"])
    generic = {"aerial", "drone", "drones", "uav", "uavs", "unmanned",
               "vehicle", "vehicles", "flight", "multi", "system", "systems"}
    members: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, list[str]] = defaultdict(list)
    cue_works = set()
    excluded_reviews = 0
    for claim in claims:
        identifier = claim["arxiv_id"]
        if identifier not in parent:
            raise ValueError("Claim outside parent")
        if cfg["exclude_review_hints"] and claim["format_hint"] == "review_or_synthesis":
            excluded_reviews += 1
            continue
        cue_works.add(identifier)
        for phrase in sentence_phrases(claim["text"], stop=stop, generic=generic,
                                       lengths=cfg["phrase_lengths"]):
            members[phrase].add(identifier)
            if len(examples[phrase]) < 3 and claim["claim_id"] not in examples[phrase]:
                examples[phrase].append(claim["claim_id"])
        if len(members) > cfg["max_unique_phrases"]:
            raise ValueError("Unique phrase cap exceeded")
    rows = []
    for phrase, ids in members.items():
        if (len(ids) < cfg["min_distinct_works"]
                or len(ids) / len(parent) > cfg["max_parent_share"]):
            continue
        recent_count = sum(parent[identifier] >= recent for identifier in ids)
        prior_count = sum(prior <= parent[identifier] < recent for identifier in ids)
        if recent_count < cfg["min_recent_works"]:
            continue
        priority = math.log1p(recent_count) * math.log2(
            1 + ((recent_count + .5) / recent_parent) /
            ((prior_count + .5) / prior_parent))
        rows.append({"phrase_en": phrase, "distinct_parent_works": len(ids),
                     "recent_works": recent_count, "prior_works": prior_count,
                     "first_submission_date_min": min(parent[identifier] for identifier in ids),
                     "review_priority_not_signal_score": round(priority, 4),
                     "example_claim_ids": examples[phrase],
                     "work_ids": sorted(ids)})
    rows.sort(key=lambda row: (-row["review_priority_not_signal_score"],
                               -row["recent_works"], row["phrase_en"]))
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "parent_works": len(parent), "cue_works": len(cue_works),
            "parent_recent_works": recent_parent, "parent_prior_works": prior_parent,
            "claim_sentences_input": len(claims),
            "review_hint_claim_sentences_excluded": excluded_reviews,
            "unique_phrases_before_filters": len(members),
            "phrases_after_filters": len(rows), "top_limit": cfg["top_limit"],
            "rows": rows[:cfg["top_limit"]],
            "production_changed": False, "relevance_verified": False,
            "weak_signal_accuracy_measured": False,
            "historical_growth_verified": False,
            "limitations": cfg["limitations"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    index_dir = (ROOT / cfg["claim_index"]).resolve()
    if (not index_dir.is_relative_to(ROOT)
            or sha256_file(index_dir / "manifest.json") != cfg["claim_index_manifest_sha256"]):
        raise ValueError("Frozen contribution index changed")
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    db_path = index_dir / manifest["file"]["name"]
    if (db_path.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(db_path) != manifest["file"]["sha256"]):
        raise ValueError("Contribution index file changed")
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        works = {row["arxiv_id"]: row["first_submission_date"]
                 for row in conn.execute("SELECT arxiv_id,first_submission_date FROM works")}
        claims = [dict(row) for row in conn.execute(
            "SELECT claim_id,arxiv_id,text,format_hint FROM claims")]
    finally:
        conn.close()
    result = propose(works, claims, cfg)
    result["config_sha256"] = sha256_file(args.config)
    result["claim_index_manifest_sha256"] = cfg["claim_index_manifest_sha256"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"parent_works": result["parent_works"],
                      "unique_phrases_before_filters": result["unique_phrases_before_filters"],
                      "phrases_after_filters": result["phrases_after_filters"],
                      "stored_top": len(result["rows"])}), flush=True)


if __name__ == "__main__":
    main()
