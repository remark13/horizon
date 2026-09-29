"""Audit all 15 arXiv phrase proposals against one complete OpenAlex query cohort.

Counts from unlike source populations remain separate. This is a relevance
diagnostic, not weak-signal detection or independent confirmation of a trend.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import json
from pathlib import Path

from saia.arxiv_metadata import _matches_phrase
from saia.controlled_collection import sha256_file


VERSION = "phrase-cross-source-support-v2"
MATCHING_VERSION = "orthographic-separators-v1"


def _matches_group(work: dict, phrases: list[str]) -> bool:
    text = f"{work.get('title') or ''} {work.get('abstract') or ''}"
    return any(_matches_phrase(text, phrase, MATCHING_VERSION)
               for phrase in phrases)


def build(*, followup_path: Path, openalex_dir: Path, mission_id: str) -> dict:
    import pyarrow.parquet as pq

    followup = json.loads(followup_path.read_text(encoding="utf-8"))
    manifest_path = openalex_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parquet_path = openalex_dir / manifest["file"]["name"]
    if (followup.get("version") != "auto-broad-phrase-full-index-followup-v2"
            or followup["groups_processed_without_manual_selection"] != 15
            or len(followup["groups"]) != 15
            or sha256_file(parquet_path) != manifest["file"]["sha256"]
            or parquet_path.stat().st_size != manifest["file"]["bytes"]):
        raise ValueError("Pinned phrase follow-up or OpenAlex cohort differs")
    cohort = next((item for item in manifest["cohorts"]
                   if item["mission_id"] == mission_id), None)
    if cohort is None or cohort["source_errors"] or cohort["excluded_invalid_rows"]:
        raise ValueError("OpenAlex mission is absent or incomplete")
    period = followup["period"]
    start_year = int(period["as_of_date_exclusive"][:4]) - 2
    start = date(start_year, 9, 1)
    end = date.fromisoformat(period["as_of_date_exclusive"])
    if (cohort["period"] != {"from": start.isoformat(),
                             "to": (end - timedelta(days=1)).isoformat()}):
        raise ValueError("OpenAlex cohort does not cover the same two complete windows")
    rows = [row for row in pq.read_table(parquet_path, columns=[
        "openalex_id", "openalex_url", "title", "abstract",
        "publication_date", "source_mission_ids",
    ]).to_pylist() if mission_id in (row["source_mission_ids"] or [])]
    if len(rows) != cohort["source_record_count"]:
        raise ValueError("OpenAlex cohort membership count differs")
    results = []
    for group in followup["groups"]:
        phrases = group["member_phrases"]
        hits = sorted((row for row in rows if _matches_group(row, phrases)),
                      key=lambda row: (row["publication_date"], row["openalex_id"]))
        title_hits = [row for row in hits if any(_matches_phrase(
            row["title"] or "", phrase, MATCHING_VERSION) for phrase in phrases)]
        counts = Counter(date.fromisoformat(row["publication_date"]).year - (
            date.fromisoformat(row["publication_date"]).month < 9) for row in hits)
        results.append({
            "rank": group["rank"], "phrase_en": group["phrase_en"],
            "member_phrases": phrases,
            "arxiv_in_broad_parent_current_text_mentions": group["in_parent_unique_ids"],
            "arxiv_full_index_current_text_mentions": group["full_index_exact_ids"],
            "openalex_query_cohort_exact_mentions": len(hits),
            "openalex_two_complete_windows": [counts[start_year], counts[start_year + 1]],
            "first_openalex_examples": [{
                "openalex_id": row["openalex_id"], "url": row["openalex_url"],
                "title": row["title"], "publication_date": row["publication_date"],
            } for row in hits[:3]],
            "openalex_title_anchored_count": len(title_hits),
            "first_openalex_title_anchored_examples": [{
                "openalex_id": row["openalex_id"], "url": row["openalex_url"],
                "title": row["title"], "publication_date": row["publication_date"],
            } for row in title_hits[:3]],
            "paper_role_verified": False,
            "single_mechanism_verified": False,
        })
    return {
        "version": VERSION,
        "arxiv_followup_sha256": sha256_file(followup_path),
        "openalex_manifest_sha256": sha256_file(manifest_path),
        "openalex_parquet_sha256": manifest["file"]["sha256"],
        "openalex_mission_id": mission_id,
        "openalex_query_terms": cohort["query_terms"],
        "openalex_query_complete_records": cohort["source_record_count"],
        "openalex_priority_catalog_area_id": cohort.get("priority_catalog_area_id"),
        "period": {"from": start.isoformat(), "to_exclusive": end.isoformat()},
        "groups": results,
        "limitations": [
            "OpenAlex coverage is complete only for this one saved query expression.",
            "The arXiv and OpenAlex populations and search expressions differ; counts are not additive or directly comparable.",
            "Current title/abstract phrase matches do not prove one mechanism, first mention, primary work or weak signal.",
            "OpenAlex current metadata does not reconstruct the historical wording of a paper.",
            "A phrase can appear in both sources for the same work; cross-source identity was not resolved here.",
        ],
        "weak_signal_detection_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--followup", type=Path, required=True)
    parser.add_argument("--openalex-dir", type=Path, required=True)
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new immutable output path")
    report = build(followup_path=args.followup, openalex_dir=args.openalex_dir,
                   mission_id=args.mission_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"groups": len(report["groups"]),
                      "openalex_query_complete_records": report[
                          "openalex_query_complete_records"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
