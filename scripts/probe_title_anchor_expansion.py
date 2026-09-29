"""Expand every bounded title n-gram from broad cards into complete local arXiv.

This is an unsupervised *candidate retrieval* pilot, not signal classification.
No known narrow-line names are used during selection or ranking.
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
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.priority_concept_search import exact_concept_search


ROOT = Path(__file__).resolve().parents[1]
VERSION = "title-anchor-expansion-pilot-v1"
WORDS = re.compile(r"[a-z]+(?:[0-9]+)?")


def candidate_phrases(cards: list[dict], *, stop: set[str], generic: set[str]) -> dict[str, dict]:
    works = {}
    for card in cards:
        for work in card["works"]:
            prior = works.setdefault(work["work_id"], work)
            if prior["title"] != work["title"]:
                raise ValueError("Canonical work title changed across cards")
    support: dict[str, set[int]] = defaultdict(set)
    for work_id, work in works.items():
        tokens = WORDS.findall(work["title"].casefold())
        for size in (2, 3):
            for index in range(len(tokens) - size + 1):
                part = tokens[index:index + size]
                if (any(token in stop or len(token) < 3 for token in part)
                        or all(token in generic for token in part)):
                    continue
                support[" ".join(part)].add(work_id)
    return {phrase: {"source_work_ids": sorted(ids), "source_title_work_count": len(ids)}
            for phrase, ids in support.items()}


def _dates(connection: sqlite3.Connection, identifiers: list[str]) -> list[str]:
    result = []
    for offset in range(0, len(identifiers), 400):
        chunk = identifiers[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        result.extend(row[0] for row in connection.execute(
            f"SELECT first_submission_date FROM works WHERE arxiv_id IN ({placeholders})",
            chunk))
    if len(result) != len(identifiers):
        raise ValueError("Expanded arXiv IDs missing from pinned index")
    return result


def run(config_path: Path) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    if cfg.get("version") != VERSION or cfg.get("production_use") is not False:
        raise ValueError("Unknown or production-enabled anchor protocol")
    source_path = (ROOT / cfg["source"]).resolve()
    index_dir = (ROOT / cfg["index"]).resolve()
    if (not source_path.is_relative_to(ROOT) or not index_dir.is_relative_to(ROOT)
            or sha256_file(source_path) != cfg["source_sha256"]
            or sha256_file(index_dir / "manifest.json") != cfg["index_manifest_sha256"]):
        raise ValueError("Frozen source or index changed")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    stop_source = json.loads((ROOT / cfg["stopwords_source"]).read_text(encoding="utf-8"))
    phrases = candidate_phrases(source["cards"], stop=set(stop_source["stopwords"]),
                                generic=set(stop_source["generic_tokens"]))
    if len(phrases) > cfg["max_candidate_phrases"]:
        raise ValueError("Candidate phrase cap exceeded")
    # Lexicographic order is a frozen no-target selection policy. All eligible
    # phrases are visited; no hand-picked known line gets preferred treatment.
    ordered = sorted(phrases)
    source_ids = {identifier["value"] for card in source["cards"]
                  for work in card["works"] for identifier in work["identifiers"]
                  if identifier["kind"] == "arxiv"}
    connection = sqlite3.connect(f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro",
                                 uri=True)
    rows = []
    exceptions = Counter()
    started = perf_counter()
    searched = 0
    try:
        for sequence, phrase in enumerate(ordered, 1):
            if perf_counter() - started >= cfg["max_run_seconds"]:
                break
            searched += 1
            if searched % 100 == 0:
                print(json.dumps({"processed": searched, "total": len(ordered),
                                  "retained": len(rows), "seconds": round(perf_counter()-started, 1)}),
                      flush=True)
            plan = {"date_from": cfg["date_from"], "as_of_date": cfg["as_of_date"],
                    "matching_version": "orthographic-separators-v1",
                    "concept_groups": [[phrase], cfg["context_terms"]],
                    "exclusions": []}
            try:
                hit = exact_concept_search(index_dir, plan,
                                           max_matches=cfg["max_full_index_matches"])
            except ValueError as error:
                # Too broad and duplicate-guard intersections have different
                # semantics; neither is silently counted as zero evidence.
                exceptions[str(error)] += 1
                continue
            ids = hit["arxiv_ids"]
            if len(ids) < cfg["min_full_index_matches"]:
                continue
            dates = _dates(connection, ids)
            recent = sum(value >= "2024-09-01" for value in dates)
            prior = sum("2021-09-01" <= value < "2024-09-01" for value in dates)
            source_overlap = len(source_ids.intersection(ids))
            # Heuristic prioritization for review only; not a trend score.
            review_order = (math.log1p(recent) * math.log2(1 +
                            ((recent + .5) / 2) / ((prior + .5) / 3)))
            rows.append({"phrase_en": phrase, **phrases[phrase],
                         "expanded_unique_arxiv_ids": len(ids),
                         "expanded_ids": ids,
                         "source_overlap": source_overlap,
                         "outside_source": len(ids) - source_overlap,
                         "recent_two_year_count": recent,
                         "prior_three_year_count": prior,
                         "review_order_not_signal_score": round(review_order, 4),
                         "coarse_candidates": hit["audit"]["coarse_candidates"]})
    finally:
        connection.close()
    completed = searched == len(ordered)
    rows.sort(key=lambda row: (-row["review_order_not_signal_score"],
                               -row["outside_source"], row["phrase_en"]))
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "source_sha256": cfg["source_sha256"],
            "index_manifest_sha256": cfg["index_manifest_sha256"],
            "source_cards": len(source["cards"]),
            "source_unique_work_ids": len({work["work_id"] for card in source["cards"]
                                           for work in card["works"]}),
            "candidate_phrases": len(ordered), "searched_phrases": searched,
            "complete_candidate_scan": completed,
            "elapsed_seconds": round(perf_counter() - started, 3),
            "retained_phrases": len(rows), "exceptions": dict(exceptions),
            "rows": rows,
            "production_changed": False, "weak_signal_accuracy_measured": False,
            "limitations": cfg["limitations"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate_phrases": report["candidate_phrases"],
                      "searched_phrases": report["searched_phrases"],
                      "retained_phrases": report["retained_phrases"],
                      "complete_candidate_scan": report["complete_candidate_scan"],
                      "elapsed_seconds": report["elapsed_seconds"]}), flush=True)
