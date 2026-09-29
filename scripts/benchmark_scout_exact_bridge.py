"""Resumable end-to-end check of one exact, user-approved RU→EN search bridge.

The script follows the scout UI's default selected-branch route. It records
retrieval and automatic-card counts, not a claim of weak-signal accuracy.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from scripts.benchmark_scout_end_to_end import _get, _operation, _poll, _post, _save


VERSION = "scout-exact-bridge-end-to-end-v1"
QUERY = "Роботизированные манипуляции"
BRANCH_ID = "robotics/robotic-manipulation-broad"
DATE_FROM = "2021-09-01"
AS_OF_DATE = "2026-09-01"


def run(*, base: str, output: Path, resume: bool = False,
        limit_seconds: int = 1200, limit_per_source: int = 25) -> dict:
    if not 60 <= limit_seconds <= 1200:
        raise ValueError("SLO clock must be within 60–1200 seconds")
    if limit_per_source not in {25, 100}:
        raise ValueError("Only frozen 25/100 per-source pilots are supported")
    if output.exists():
        if not resume:
            raise FileExistsError("Existing run requires --resume")
        state = json.loads(output.read_text(encoding="utf-8"))
        if (state.get("version") != VERSION or state.get("query_ru") != QUERY
                or state.get("policy", {}).get("limit_per_source") != limit_per_source):
            raise ValueError("Existing run has different inputs")
        if state.get("status") in {"succeeded", "no_works", "source_error_zero_works",
                                   "no_eligible_publications"}:
            return state
    else:
        state = {
            "version": VERSION, "query_ru": QUERY,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "limit_seconds": limit_seconds, "status": "started", "events": [],
            "policy": {
                "same_search_steps_as_scout_ui": limit_per_source == 25,
                "expanded_collection_pilot": limit_per_source == 100,
                "one_selected_exact_bridge": BRANCH_ID,
                "original_query_preserved": True,
                "date_from": DATE_FROM, "as_of_date": AS_OF_DATE,
                "limit_per_source": limit_per_source, "max_results": 300,
                "openalex_collection_mode": "live_with_cache_fallback",
                "zero_is_not_absence_of_science": True,
                "counts_are_not_accuracy": True,
            },
        }
        _save(output, state)
    started_epoch = datetime.fromisoformat(state["started_at"]).timestamp()
    deadline = time.monotonic() + max(0, limit_seconds - (time.time() - started_epoch))
    if time.monotonic() >= deadline:
        state["status"] = "deadline_exceeded_job_continues"
        _save(output, state)
        return state
    if "preview_sha256" not in state:
        preview = _post(base, "/query-plan/preview",
                        {"query": QUERY, "max_suggestions": 12})
        suggestions = preview.get("suggestions") or []
        if len(suggestions) != 1 or suggestions[0]["suggestion_id"] != BRANCH_ID:
            raise ValueError("Exact bridge preview changed")
        bridge = suggestions[0]
        if bridge.get("basis") != "controlled_exact_search_bridge":
            raise ValueError("Unexpected bridge basis")
        state["preview_sha256"] = preview["plan_payload_sha256"]
        state["phrases_en"] = bridge["phrases_en"]
        _save(output, state)
    if "plan_id" not in state:
        approved = _post(base, "/query-plan/approve", {
            "query": QUERY, "max_suggestions": 12,
            "preview_payload_sha256": state["preview_sha256"],
            "selected_branch_ids": [BRANCH_ID],
            "approved_by": "goal-exact-bridge-smoke",
            "operation_id": _operation(state, "approve", output),
        })
        state["plan_id"] = approved["plan_id"]
        state["approved_branches"] = approved["approved_plan"]["branches"]
        if {b["branch_id"] for b in state["approved_branches"]} != {
                "original-query", BRANCH_ID}:
            raise ValueError("Unexpected approved branches")
        _save(output, state)
    if "compilation_id" not in state:
        compiled = _post(base, "/query-plans/" + state["plan_id"] + "/compile", {
            "branch_specs": [
                {"branch_id": "original-query", "included_phrases": [QUERY],
                 "excluded_phrases": []},
                {"branch_id": BRANCH_ID,
                 "included_phrases": state["phrases_en"], "excluded_phrases": []},
            ],
            "compiled_by": "goal-exact-bridge-smoke",
            "operation_id": _operation(state, "compile", output),
        })
        state["compilation_id"] = compiled["compilation_id"]
        _save(output, state)
    if "discovery_job_id" not in state:
        discovery = _post(base, "/query-plans/" + state["plan_id"] +
                          "/jobs/discovery", {
            "requested_by": "goal-exact-bridge-smoke",
            "date_from": DATE_FROM, "as_of_date": AS_OF_DATE,
            "limit_per_source": limit_per_source, "max_results": 300,
            "compiled_query_plan_id": state["compilation_id"],
            "openalex_collection_mode": "live_with_cache_fallback",
            "operation_id": _operation(state, "discovery", output),
        })
        state["discovery_job_id"] = discovery["job_id"]
        _save(output, state)
    collected = _poll(base, state, output, state["discovery_job_id"],
                      "collect", deadline)
    if collected is None:
        return state
    result = collected.get("result") or {}
    works = result.get("works")
    if not isinstance(works, list):
        raise ValueError("Discovery did not expose bounded works")
    state["bounded_discovery_works"] = len(works)
    state["source_counts"] = {source: sum(
        source in (work.get("sources") or []) for work in works)
        for source in ("openalex", "arxiv")}
    state["source_errors"] = result.get("errors") or {}
    _save(output, state)
    if not works:
        state["status"] = ("source_error_zero_works" if state["source_errors"]
                           else "no_works")
    else:
        if "analysis_job_id" not in state:
            analysis = _post(base, "/jobs/" + state["discovery_job_id"] +
                             "/full-analysis", {
                "requested_by": "goal-exact-bridge-smoke",
                "max_records": 10000, "top_n": 100,
                "operation_id": _operation(state, "analysis", output),
            })
            state["analysis_job_id"] = analysis["analysis_job"]["job_id"]
            _save(output, state)
        analysed = _poll(base, state, output, state["analysis_job_id"],
                         "analyze", deadline)
        if analysed is None:
            return state
        analysed_result = analysed.get("result") or {}
        state["status"] = analysed_result.get("input_status") or "succeeded"
        state["mission_id"] = analysed.get("mission_id")
        state["score_run_id"] = (analysed_result.get("runs") or {}).get("score")
        state["quality_counts"] = (analysed_result.get("quality") or {}).get("counts")
        if state["mission_id"] and state["score_run_id"]:
            triage = _get(base, "/triage/" + state["mission_id"] +
                          "?score_run_id=" + str(state["score_run_id"]) + "&limit=100")
            state["automatic_cards"] = triage["counts"]["all_cards"]
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    state["elapsed_seconds"] = time.time() - started_epoch
    state["within_20_minutes"] = state["elapsed_seconds"] <= 1200
    _save(output, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit-seconds", type=int, default=1200)
    parser.add_argument("--limit-per-source", type=int, default=25,
                        choices=(25, 100))
    args = parser.parse_args()
    state = run(base=args.base, output=args.output, resume=args.resume,
                limit_seconds=args.limit_seconds,
                limit_per_source=args.limit_per_source)
    print(json.dumps({key: state.get(key) for key in (
        "status", "discovery_job_id", "analysis_job_id", "bounded_discovery_works",
        "source_counts", "automatic_cards", "elapsed_seconds", "within_20_minutes")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
