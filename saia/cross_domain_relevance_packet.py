"""Blind, unlabelled article-topic review packet from frozen source probes.

The packet hides search order and match status. A separate audit retains the
sampling strata, source IDs, report hashes and zero/too-broad cases.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from saia.controlled_collection import sha256_file


VERSION = "cross-domain-article-relevance-packet-v1"
AUDIT_VERSION = "cross-domain-article-relevance-audit-v1"
SEED = "saia-goal-cross-domain-review-2026-09-25-v1"


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _pick(values: list, case_id: str, stratum: str, key, cap: int) -> list:
    return sorted(values, key=lambda value: sha256(
        f"{SEED}\0{case_id}\0{stratum}\0{key(value)}".encode()).hexdigest())[:cap]


def _item_id(case_id: str, source: str, source_id: str) -> str:
    return "review-" + sha256(
        f"{SEED}\0{case_id}\0{source}\0{source_id}".encode()).hexdigest()[:24]


def _openalex_source_id(work: dict) -> str:
    values = work.get("source_ids")
    if (not isinstance(values, list) or not values
            or not isinstance(values[0], str)
            or not values[0].startswith("https://openalex.org/W")):
        raise ValueError("OpenAlex source identity is missing")
    return values[0]


def build(*, plan_path: Path, catalog_path: Path, arxiv_report_path: Path,
          openalex_report_path: Path, followup_path: Path,
          index_dir: Path) -> tuple[dict, dict]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    arxiv = json.loads(arxiv_report_path.read_text(encoding="utf-8"))
    openalex = json.loads(openalex_report_path.read_text(encoding="utf-8"))
    followup = json.loads(followup_path.read_text(encoding="utf-8"))
    index_manifest = index_dir / "manifest.json"
    if (plan.get("version") != "goal-cross-domain-compound-pilot-v1"
            or catalog.get("version") != "priority-catalog-v1"
            or arxiv.get("version") != "goal-cross-domain-compound-arxiv-probe-v1"
            or openalex.get("version") != "goal-cross-domain-openalex-followup-v1"
            or arxiv["config_sha256"] != sha256_file(plan_path)
            or openalex["case_plan_sha256"] != sha256_file(plan_path)
            or openalex["live_config_sha256"] != sha256_file(followup_path)
            or followup.get("version") != "goal-cross-domain-openalex-followup-v1"
            or followup["frozen_compound_plan_sha256"] != sha256_file(plan_path)
            or openalex.get("query_mode") != "boolean"
            or arxiv["catalog_sha256"] != sha256_file(catalog_path)
            or arxiv["index_manifest_sha256"] != sha256_file(index_manifest)
            or arxiv["period"] != openalex["period"]
            or arxiv["period"] != {"from": followup["date_from"],
                                   "as_of_exclusive": followup["as_of_date"]}):
        raise ValueError("Review sources do not share the frozen pilot inputs")
    cases = {case["case_id"]: case for case in plan["cases"]}
    if (len(cases) != 22 or {row["case_id"] for row in arxiv["rows"]} != set(cases)
            or len(arxiv["rows"]) != 22
            or [row["case_id"] for row in openalex["rows"]]
            != [row["case_id"] for row in followup["cases"]]):
        raise ValueError("The complete 22-case arXiv probe is required")
    original_topics = {
        row["id"]: row["title_original"] for row in catalog["customer_examples"]}
    items = []
    audit_rows = []
    connection = sqlite3.connect(
        f"file:{(index_dir / 'index.sqlite3').resolve()}?mode=ro", uri=True)
    try:
        for row in arxiv["rows"]:
            case = cases[row["case_id"]]
            if row["status"] != "exact_index_probe_complete":
                audit_rows.append({"case_id": row["case_id"], "source": "arxiv",
                                   "status": row["status"], "selected": 0,
                                   "reason": row.get("reason")})
                continue
            ids = row["arxiv_ids"]
            if len(ids) != row["match_count"] or len(ids) != len(set(ids)):
                raise ValueError("arXiv cohort count or identity changed")
            chosen = _pick(ids, row["case_id"], "strict", lambda value: value, 4)
            for arxiv_id in chosen:
                document = connection.execute(
                    "SELECT title,abstract,first_submission_date FROM works "
                    "WHERE arxiv_id=?", (arxiv_id,)).fetchone()
                if document is None:
                    raise ValueError("Frozen arXiv work missing from index")
                identifier = _item_id(row["case_id"], "arxiv", arxiv_id)
                items.append({"item_id": identifier, "case_id": row["case_id"],
                              "target_topic": original_topics.get(row["case_id"],
                                                                  case["query_ru"]),
                              "document": {"source": "arxiv", "source_id": arxiv_id,
                                           "url": f"https://arxiv.org/abs/{arxiv_id}",
                                           "title": document[0], "abstract": document[1],
                                           "published_at": document[2]},
                              "review": {"topical_relevance": None,
                                         "evidence_role": None,
                                         "missing_qualifiers": None,
                                         "rationale": None}})
                audit_rows.append({"item_id": identifier, "case_id": row["case_id"],
                                   "source": "arxiv", "source_id": arxiv_id,
                                   "stratum": "strict_index_match"})
            if not chosen:
                audit_rows.append({"case_id": row["case_id"], "source": "arxiv",
                                   "status": "zero_literal_matches", "selected": 0})
        openalex_ids = set()
        for row in openalex["rows"]:
            case_id = row["case_id"]
            if case_id not in cases or row["errors"]:
                raise ValueError("OpenAlex case or API result invalid")
            works = row["works"]
            if (len(works) != row["returned_count"]
                    or sum(w["strict_compound_match"] for w in works)
                    != row["strict_compound_count"]):
                raise ValueError("OpenAlex result counts changed")
            for stratum, subset, cap in (
                ("strict_title_abstract_match",
                 [work for work in works if work["strict_compound_match"]], 4),
                ("api_only_rejected_by_title_abstract",
                 [work for work in works if not work["strict_compound_match"]], 1),
            ):
                for work in _pick(subset, case_id, stratum,
                                  _openalex_source_id, cap):
                    source_id = _openalex_source_id(work)
                    if (case_id, source_id) in openalex_ids:
                        raise ValueError("OpenAlex source identity is missing or repeated")
                    openalex_ids.add((case_id, source_id))
                    identifier = _item_id(case_id, "openalex", source_id)
                    items.append({"item_id": identifier, "case_id": case_id,
                                  "target_topic": original_topics.get(case_id,
                                                                      cases[case_id]["query_ru"]),
                                  "document": {"source": "openalex", "source_id": source_id,
                                               "url": (work["urls"][0] if work["urls"] else source_id),
                                               "title": work["title"],
                                               "abstract": work["abstract"],
                                               "published_at": work["published_at"]},
                                  "review": {"topical_relevance": None,
                                             "evidence_role": None,
                                             "missing_qualifiers": None,
                                             "rationale": None}})
                    audit_rows.append({"item_id": identifier, "case_id": case_id,
                                       "source": "openalex", "source_id": source_id,
                                       "stratum": stratum})
    finally:
        connection.close()
    items.sort(key=lambda row: sha256(f"{SEED}\0{row['item_id']}".encode()).hexdigest())
    if len({item["item_id"] for item in items}) != len(items):
        raise ValueError("Review packet has repeated item IDs")
    packet = {"version": VERSION, "sampling_seed": SEED,
              "target_policy": "customer original title; otherwise frozen pilot query",
              "items": items,
              "review_choices": {"topical_relevance": ["yes", "partial", "no", "uncertain"],
                                 "evidence_role": ["primary_technical_result", "review",
                                                   "application_or_case", "background_only",
                                                   "market_or_infrastructure", "uncertain"]},
              "limits": {"contains_no_completed_labels": True,
                         "reviewer_does_not_see_source_rank_or_match_stratum": True,
                         "is_not_independent_signal_discovery": True,
                         "current_abstract_not_historical_first_version": True,
                         "full_text_not_reviewed": True}}
    audit = {"version": AUDIT_VERSION, "packet_sha256": sha256(json_bytes(packet)).hexdigest(),
             "plan_sha256": sha256_file(plan_path),
             "catalog_sha256": sha256_file(catalog_path),
             "arxiv_report_sha256": sha256_file(arxiv_report_path),
             "openalex_report_sha256": sha256_file(openalex_report_path),
             "followup_config_sha256": sha256_file(followup_path),
             "index_manifest_sha256": sha256_file(index_manifest),
             "rows": audit_rows,
             "sampling_policy": {"per_arxiv_case_cap": 4,
                                 "per_openalex_case_strict_cap": 4,
                                 "per_openalex_case_api_only_cap": 1,
                                 "deterministic_without_replacement": True,
                                 "selected_after_retrieval_pilot": True,
                                 "no_precision_until_review_completed": True}}
    return packet, audit
