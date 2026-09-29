"""Resumable UI-equivalent smoke for frozen free-Russian no-suggestion cases.

Creates real SAIA jobs but never modifies expert reviews. A zero result is
reported as a retrieval failure for this specific policy, not no science.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from saia.controlled_collection import sha256_file
from scripts.benchmark_scout_end_to_end import _get, _operation, _poll, _post, _save


VERSION = "scout-no-suggestions-end-to-end-v1"


def run(*, config_path: Path, case_id: str, base: str, output: Path,
        resume: bool = False, limit_seconds: int = 1200) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    version = config.get("version")
    cases_count = len(config.get("cases") or [])
    if (version not in {"scout-no-suggestions-slo-pilot-v1",
                        "scout-no-suggestions-slo-pilot-v2"}
            or (version.endswith("v1") and cases_count != 2)
            or (version.endswith("v2") and not 1 <= cases_count <= 10)
            or config.get("openalex_collection_mode") not in {
                "cache_year_spread", "live_with_cache_fallback"}
            or config.get("limit_per_source") != 25
            or config.get("max_results") != 300):
        raise ValueError("Unexpected frozen free-query SLO configuration")
    cases = {case["case_id"]: case for case in config["cases"]}
    if len(cases) != cases_count or case_id not in cases:
        raise ValueError("Unknown or repeated free-query case")
    if not 60 <= limit_seconds <= 1200:
        raise ValueError("SLO clock must be within 60–1200 seconds")
    if output.exists():
        if not resume:
            raise FileExistsError("Existing benchmark requires --resume")
        state = json.loads(output.read_text(encoding="utf-8"))
        if (state.get("version") != VERSION or state.get("case_id") != case_id
                or state.get("config_sha256") != sha256_file(config_path)):
            raise ValueError("Existing benchmark has different inputs")
        if state.get("status") in {"succeeded", "no_works", "source_error_zero_works",
                                   "no_eligible_publications"}:
            return state
    else:
        state = {"version": VERSION, "case_id": case_id,
                 "query_ru": cases[case_id]["query_ru"],
                 "role": cases[case_id]["role"],
                 "config_sha256": sha256_file(config_path),
                 "started_at": datetime.now(timezone.utc).isoformat(),
                 "limit_seconds": limit_seconds,
                 "status": "started", "events": [],
                 "policy": {"same_search_steps_as_scout_ui": True,
                            "openalex_collection_mode": config["openalex_collection_mode"],
                            "no_english_phrase_supplied": True,
                            "zero_is_not_absence_of_science": True,
                            "not_p50_or_p95": True}}
        _save(output, state)
    started_epoch = datetime.fromisoformat(state["started_at"]).timestamp()
    deadline = time.monotonic() + max(0, limit_seconds - (time.time() - started_epoch))
    if time.monotonic() >= deadline:
        state["status"] = "deadline_exceeded_job_continues"
        _save(output, state)
        return state
    query = state["query_ru"]
    if "preview_sha256" not in state:
        preview = _post(base, "/query-plan/preview",
                        {"query": query, "max_suggestions": 12})
        if preview.get("suggestions"):
            raise ValueError("Case no longer follows the no-suggestions UI route")
        state["preview_sha256"] = preview["plan_payload_sha256"]
        _save(output, state)
    if "plan_id" not in state:
        approval = _post(base, "/query-plan/approve", {
            "query": query, "max_suggestions": 12,
            "preview_payload_sha256": state["preview_sha256"],
            "selected_branch_ids": [], "approved_by": "goal-free-query-slo",
            "operation_id": _operation(state, "approve", output)})
        branches = approval["approved_plan"]["branches"]
        if len(branches) != 1 or branches[0]["branch_id"] != "original-query":
            raise ValueError("Unexpected approved branches")
        state["plan_id"] = approval["plan_id"]
        _save(output, state)
    if "compilation_id" not in state:
        compiled = _post(base, "/query-plans/" + state["plan_id"] + "/compile", {
            "branch_specs": [{"branch_id": "original-query",
                              "included_phrases": [query], "excluded_phrases": []}],
            "compiled_by": "goal-free-query-slo",
            "operation_id": _operation(state, "compile", output)})
        state["compilation_id"] = compiled["compilation_id"]
        _save(output, state)
    if "discovery_job_id" not in state:
        discovery = _post(base, "/query-plans/" + state["plan_id"] +
                          "/jobs/discovery", {
            "requested_by": "goal-free-query-slo",
            "date_from": config["date_from"],
            "as_of_date": config["as_of_date"],
            "limit_per_source": config["limit_per_source"],
            "max_results": config["max_results"],
            "compiled_query_plan_id": state["compilation_id"],
            "openalex_collection_mode": config["openalex_collection_mode"],
            "operation_id": _operation(state, "discovery", output)})
        state["discovery_job_id"] = discovery["job_id"]
        _save(output, state)
    discovery_job = _poll(base, state, output, state["discovery_job_id"],
                          "collect", deadline)
    if discovery_job is None:
        return state
    result = discovery_job.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("works"), list):
        raise ValueError("Completed discovery did not expose its bounded works")
    state["bounded_discovery_works"] = len(result["works"])
    state["source_errors"] = result.get("errors") or {}
    if not result["works"]:
        state["status"] = ("source_error_zero_works" if state["source_errors"]
                           else "no_works")
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        state["elapsed_seconds"] = time.time() - started_epoch
        state["within_20_minutes"] = state["elapsed_seconds"] <= 1200
        _save(output, state)
        return state
    if "analysis_job_id" not in state:
        analysis = _post(base, "/jobs/" + state["discovery_job_id"] +
                         "/full-analysis", {
            "requested_by": "goal-free-query-slo", "max_records": 10000,
            "top_n": 100, "operation_id": _operation(state, "analysis", output)})
        state["analysis_job_id"] = analysis["analysis_job"]["job_id"]
        _save(output, state)
    analysis_job = _poll(base, state, output, state["analysis_job_id"],
                         "analyze", deadline)
    if analysis_job is None:
        return state
    state["status"] = ((analysis_job.get("result") or {}).get("input_status")
                       or "succeeded")
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    state["elapsed_seconds"] = time.time() - started_epoch
    state["within_20_minutes"] = state["elapsed_seconds"] <= 1200
    state["mission_id"] = analysis_job.get("mission_id")
    state["score_run_id"] = (analysis_job.get("result") or {}).get(
        "runs", {}).get("score")
    state["quality_counts"] = (analysis_job.get("result") or {}).get(
        "quality", {}).get("counts")
    if state["mission_id"] and state["score_run_id"]:
        triage = _get(base, "/triage/" + state["mission_id"] +
                      "?score_run_id=" + str(state["score_run_id"]) + "&limit=100")
        state["automatic_cards"] = triage["counts"]["all_cards"]
    _save(output, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit-seconds", type=int, default=1200)
    args = parser.parse_args()
    state = run(config_path=args.config, case_id=args.case_id,
                base=args.base, output=args.output, resume=args.resume,
                limit_seconds=args.limit_seconds)
    print(json.dumps({key: state.get(key) for key in
                      ("case_id", "status", "discovery_job_id", "analysis_job_id",
                       "bounded_discovery_works", "automatic_cards",
                       "elapsed_seconds", "within_20_minutes")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
