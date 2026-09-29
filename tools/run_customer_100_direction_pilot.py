"""Bounded, reproducible six-direction pilot for the customer seed workbook.

This is a product/retrieval diagnostic, not weak-signal accuracy measurement.
The six broad queries below are fixed before reading system results and do not
contain any of the 100 individual expected signal names.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import requests


QUERIES = [
    ("Индустриальный ИИ", "industrial artificial intelligence",
     ["industrial artificial intelligence", "machine learning for manufacturing", "AI in industrial automation"]),
    ("Роботы", "robotics",
     ["robotics", "autonomous robots", "robotic systems"]),
    ("Инфраструктура ИИ", "artificial intelligence infrastructure",
     ["artificial intelligence infrastructure", "machine learning systems", "AI data centers"]),
    ("Финтех", "financial technology",
     ["financial technology", "fintech", "AI in financial services"]),
    ("Защита ИИ", "artificial intelligence security",
     ["artificial intelligence security", "AI model security", "adversarial machine learning"]),
    ("Edge", "edge artificial intelligence",
     ["edge artificial intelligence", "edge AI", "on-device machine learning"]),
]

DATE_FROM = "2021-09-24"
AS_OF_DATE = "2026-09-24"


def call(session: requests.Session, base: str, method: str, route: str,
         payload: dict | None = None) -> dict:
    response = session.request(method, base + route, json=payload, timeout=40)
    if not response.ok:
        raise RuntimeError(f"{method} {route}: {response.status_code} {response.text[:500]}")
    return response.json()


def wait_job(session: requests.Session, base: str, job_id: str,
             max_wait_seconds: int) -> dict:
    deadline = time.monotonic() + max_wait_seconds
    prior = None
    while time.monotonic() < deadline:
        job = call(session, base, "GET", f"/jobs/{job_id}")
        if job["status"] != prior:
            print(f"  job {job_id}: {job['status']}", flush=True)
            prior = job["status"]
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(3)
    raise TimeoutError(f"Job {job_id} did not finish in {max_wait_seconds}s")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--job-timeout", type=int, default=900)
    parser.add_argument("--only-area", action="append", default=[],
                        help="Run only the named workbook area; repeat as needed")
    parser.add_argument("--discovery-only", action="store_true",
                        help="Stop after bounded source collection; skip intensive embeddings")
    parser.add_argument("--theme-plan", action="store_true",
                        help="Use the current explicitly approved customer-informed theme branches")
    args = parser.parse_args()
    selected_queries = [item for item in QUERIES
                        if not args.only_area or item[0] in args.only_area]
    if args.only_area and len(selected_queries) != len(set(args.only_area)):
        raise SystemExit("Unknown or duplicate --only-area value")
    if args.output.exists():
        raise SystemExit("Output directory already exists; this pilot is immutable")
    args.output.mkdir(parents=True)
    workbook_hash = hashlib.sha256(args.workbook.read_bytes()).hexdigest()
    workbook = openpyxl.load_workbook(args.workbook, read_only=True, data_only=True)
    rows = list(workbook.active.iter_rows(min_row=3, values_only=True))
    areas = Counter(str(row[3]) for row in rows if row[2])
    manifest = {
        "version": "customer-100-direction-pilot-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workbook_name": args.workbook.name,
        "workbook_sha256": workbook_hash,
        "reference_rows": len(rows),
        "reference_areas": dict(sorted(areas.items())),
        "query_policy": (
            "customer-informed controlled subthemes; in-sample functional retest, not independent recall"
            if args.theme_plan else
            "category-level English wording fixed before seeing results; no individual target names"
        ),
        "theme_plan": args.theme_plan,
        "date_from": DATE_FROM,
        "as_of_date": AS_OF_DATE,
        "limit_per_source_per_branch": 25,
        "max_merged_results": 100,
        "full_analysis_max_records": 1000,
        "top_n": 30,
        "discovery_only": args.discovery_only,
        "source_note": "Current OpenAlex metadata plus pinned local arXiv; not a complete field census",
        "queries": [
            {"area": area, "query_en": query, "local_arxiv_phrases": phrases}
            for area, query, phrases in selected_queries
        ],
    }
    write_json(args.output / "manifest.json", manifest)
    session = requests.Session()
    call(session, args.base_url, "GET", "/health")
    result = {"manifest_sha256": hashlib.sha256((args.output / "manifest.json").read_bytes()).hexdigest(),
              "areas": {}}
    for area, query, phrases in selected_queries:
        print(f"AREA {area}", flush=True)
        search_query = area if args.theme_plan else query
        entry = {"query": search_query, "query_en": query,
                 "local_arxiv_phrases": phrases}
        result["areas"][area] = entry
        try:
            preview = call(session, args.base_url, "POST", "/query-plan/preview",
                           {"query": search_query, "max_suggestions": 12})
            selected_ids = ([item["suggestion_id"] for item in preview["suggestions"]]
                            if args.theme_plan else [])
            approved = call(session, args.base_url, "POST", "/query-plan/approve", {
                "query": search_query, "max_suggestions": 12,
                "preview_payload_sha256": preview["plan_payload_sha256"],
                "selected_branch_ids": selected_ids,
                "approved_by": "customer-100-direction-pilot",
                "operation_id": str(uuid.uuid4()),
            })
            entry["plan_id"] = approved["plan_id"]
            branch_specs = [{"branch_id": "original-query",
                             "included_phrases": [search_query if args.theme_plan else phrases[0]],
                             "excluded_phrases": []}]
            if not args.theme_plan:
                branch_specs[0]["included_phrases"] = phrases
            else:
                branch_specs.extend({
                    "branch_id": item["suggestion_id"],
                    "included_phrases": item["phrases_en"],
                    "excluded_phrases": [],
                } for item in preview["suggestions"])
            compiled = call(session, args.base_url, "POST",
                            f"/query-plans/{approved['plan_id']}/compile", {
                                "branch_specs": branch_specs,
                                "compiled_by": "customer-100-direction-pilot",
                                "operation_id": str(uuid.uuid4()),
                            })
            entry["compilation_id"] = compiled["compilation_id"]
            discovery = call(session, args.base_url, "POST",
                             f"/query-plans/{approved['plan_id']}/jobs/discovery", {
                                 "requested_by": "customer-100-direction-pilot",
                                 "date_from": DATE_FROM, "as_of_date": AS_OF_DATE,
                                 "limit_per_source": 25, "max_results": 100,
                                 "compiled_query_plan_id": compiled["compilation_id"],
                                 "operation_id": str(uuid.uuid4()), "max_attempts": 1,
                             })
            entry["discovery_job_id"] = discovery["job_id"]
            collected = wait_job(session, args.base_url, discovery["job_id"], args.job_timeout)
            entry["discovery_status"] = collected["status"]
            if collected["status"] != "succeeded":
                entry["error"] = collected.get("error")
                continue
            collected_result = collected.get("result") or {}
            entry["collection"] = {
                "branch_audit": collected_result.get("branches"),
                "merge": {key: value for key, value in (collected_result.get("merge") or {}).items()
                          if key != "results"},
                "local_arxiv_audit": {key: value for key, value in
                                      (collected_result.get("local_arxiv_audit") or {}).items()
                                      if key != "branches"},
                "errors": collected_result.get("errors"),
            }
            if args.discovery_only:
                continue
            analysis = call(session, args.base_url, "POST",
                            f"/jobs/{discovery['job_id']}/full-analysis", {
                                "requested_by": "customer-100-direction-pilot",
                                "max_records": 1000, "top_n": 30,
                                "operation_id": str(uuid.uuid4()), "max_attempts": 1,
                            })
            analysis_job = analysis["analysis_job"]
            entry["analysis_job_id"] = analysis_job["job_id"]
            analyzed = wait_job(session, args.base_url, analysis_job["job_id"], args.job_timeout)
            entry["analysis_status"] = analyzed["status"]
            if analyzed["status"] != "succeeded":
                entry["error"] = analyzed.get("error")
                continue
            entry["mission_id"] = analyzed.get("mission_id")
            entry["analysis_result"] = analyzed.get("result")
            score = ((analyzed.get("result") or {}).get("runs") or {}).get("score")
            if not score or not entry["mission_id"]:
                entry["error"] = "Missing score or mission in completed analysis"
                continue
            entry["score_run_id"] = score
            triage = call(session, args.base_url, "GET",
                          f"/triage/{entry['mission_id']}?score_run_id={score}&limit=100")
            entry["triage"] = {
                "total": triage.get("total"), "returned": triage.get("returned"),
                "version": triage.get("version"),
                "queue": [{
                    "rank": item.get("rank"), "screening": item.get("screening"),
                    "card": {key: value for key, value in (item.get("card") or {}).items()
                             if key in {"candidate_id", "label", "first_found", "research_birth",
                                        "metrics", "evidence", "limitations"}},
                } for item in triage.get("queue") or []],
            }
        except Exception as error:
            entry["error"] = f"{type(error).__name__}: {error}"[:1000]
            print(f"  ERROR {entry['error']}", flush=True)
        finally:
            write_json(args.output / "results.json", result)
    print(f"DONE {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
