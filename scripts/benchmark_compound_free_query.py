"""Resumable bounded API smoke for one developer-reviewed free-query concept plan.

The plan is fixed before the run. It is not an independently labeled quality
benchmark, and a model proposal is never sent directly to production.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from scripts.benchmark_scout_end_to_end import _get, _operation, _poll, _post, _save


VERSION = "compound-free-query-api-smoke-v1"
QUERY = "нейроморфные чипы для периферийных устройств"
CONCEPT_GROUPS = [
    ["neuromorphic chips", "neuromorphic processors"],
    ["edge devices", "on-device inference"],
]


def run(*, base: str, output: Path, resume: bool, limit_seconds: int = 1200,
        variant: str = "compound") -> dict:
    if not 60 <= limit_seconds <= 1200:
        raise ValueError("Limit must be 60–1200 seconds")
    if variant not in {"compound", "legacy", "compound_boolean"}:
        raise ValueError("Unknown fixed benchmark variant")
    if output.exists():
        if not resume:
            raise FileExistsError("Existing run requires --resume")
        state = json.loads(output.read_text(encoding="utf-8"))
        if (state.get("version") != VERSION or state.get("query_ru") != QUERY
                or state.get("variant", "compound") != variant):
            raise ValueError("Unknown run state")
        if state.get("status") in {"succeeded", "no_works"}:
            return state
    else:
        state = {"version": VERSION, "query_ru": QUERY, "variant": variant,
                 "concept_groups_dev_reviewed": CONCEPT_GROUPS if variant != "legacy" else [],
                 "started_at": datetime.now(timezone.utc).isoformat(),
                 "limit_seconds": limit_seconds, "events": [], "status": "started",
                 "policy": {"customer_example_used_as_quality_label": False,
                            "model_proposal_executed_unreviewed": False,
                            "one_query_not_latency_distribution": True,
                            "full_live_openalex_recall_proven": False}}
        _save(output, state)
    started_epoch = datetime.fromisoformat(state["started_at"]).timestamp()
    deadline = time.monotonic() + max(0, limit_seconds - (time.time() - started_epoch))
    if time.monotonic() >= deadline:
        state["status"] = "deadline_exceeded_job_continues"
        _save(output, state)
        return state
    if "preview_sha256" not in state:
        preview = _post(base, "/query-plan/preview", {"query": QUERY, "max_suggestions": 12})
        state["preview_sha256"] = preview["plan_payload_sha256"]
        state["suggestions_seen"] = len(preview.get("suggestions") or [])
        _save(output, state)
    if "plan_id" not in state:
        approved = _post(base, "/query-plan/approve", {
            "query": QUERY, "max_suggestions": 12,
            "preview_payload_sha256": state["preview_sha256"],
            "selected_branch_ids": [], "approved_by": "goal-compound-smoke",
            "operation_id": _operation(state, "approve", output)})
        state["plan_id"] = approved["plan_id"]
        _save(output, state)
    if "compilation_id" not in state:
        branch_spec = {"branch_id": "original-query",
                       "included_phrases": [QUERY], "excluded_phrases": []}
        if variant != "legacy":
            branch_spec["concept_groups"] = CONCEPT_GROUPS
        compiled = _post(base, "/query-plans/" + state["plan_id"] + "/compile", {
            "branch_specs": [branch_spec],
            "compiled_by": "goal-compound-smoke",
            "operation_id": _operation(state, "compile", output)})
        state["compilation_id"] = compiled["compilation_id"]
        state["compilation_version"] = compiled["compiled_plan"]["version"]
        _save(output, state)
    if "discovery_job_id" not in state:
        job = _post(base, "/query-plans/" + state["plan_id"] + "/jobs/discovery", {
            "requested_by": "goal-compound-smoke", "date_from": "2021-09-01",
            "as_of_date": "2026-09-01", "limit_per_source": 25,
            "max_results": 300, "compiled_query_plan_id": state["compilation_id"],
            "openalex_collection_mode": (
                "compound_boolean_live" if variant == "compound_boolean"
                else "cache_year_spread"),
            "operation_id": _operation(state, "discovery", output)})
        state["discovery_job_id"] = job["job_id"]
        _save(output, state)
    discovery = _poll(base, state, output, state["discovery_job_id"], "collect", deadline)
    if discovery is None:
        return state
    works = (discovery.get("result") or {}).get("works") or []
    state["discovery_work_count"] = len(works)
    state["discovery_errors"] = (discovery.get("result") or {}).get("errors") or {}
    state["discovery_branch_audit"] = (discovery.get("result") or {}).get("branches") or []
    _save(output, state)
    if not works:
        state["status"] = "no_works"
        state["elapsed_seconds"] = time.time() - started_epoch
        _save(output, state)
        return state
    if "analysis_job_id" not in state:
        analysis = _post(base, "/jobs/" + state["discovery_job_id"] + "/full-analysis", {
            "requested_by": "goal-compound-smoke", "max_records": 10000,
            "top_n": 100, "operation_id": _operation(state, "analysis", output)})
        state["analysis_job_id"] = analysis["analysis_job"]["job_id"]
        _save(output, state)
    result = _poll(base, state, output, state["analysis_job_id"], "analyze", deadline)
    if result is None:
        return state
    state["status"] = "succeeded"
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    state["elapsed_seconds"] = time.time() - started_epoch
    state["within_20_minutes"] = state["elapsed_seconds"] <= 1200
    state["mission_id"] = result.get("mission_id")
    state["score_run_id"] = (result.get("result") or {}).get("runs", {}).get("score")
    if state["mission_id"] and state["score_run_id"]:
        packet = _get(base, "/signals/" + state["mission_id"] +
                      "?score_run_id=" + str(state["score_run_id"]))
        state["card_count"] = len(packet.get("cards") or [])
        state["candidate_status"] = (
            "candidates_returned" if state["card_count"] else "analysis_succeeded_no_candidates")
    _save(output, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit-seconds", type=int, default=1200)
    parser.add_argument("--variant", choices=["compound", "legacy", "compound_boolean"], default="compound")
    args = parser.parse_args()
    result = run(base=args.base, output=args.output, resume=args.resume,
                 limit_seconds=args.limit_seconds, variant=args.variant)
    print(json.dumps({"status": result["status"],
                      "discovery_work_count": result.get("discovery_work_count"),
                      "score_run_id": result.get("score_run_id"),
                      "card_count": result.get("card_count"),
                      "candidate_status": result.get("candidate_status"),
                      "elapsed_seconds": result.get("elapsed_seconds")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
