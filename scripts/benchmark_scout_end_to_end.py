"""Resumable, bounded scout-UI-equivalent smoke with an explicit 20-minute clock.

This creates real SAIA jobs. It does not modify source data or expert reviews.
The run state is updated atomically so an interrupted process can resume the
same idempotent API operations with --resume rather than launch a new search.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


BRANCH_PHRASES = {
    "unmanned-aircraft-systems/uas-airframes": [
        "UAV airframe", "drone airframe", "morphing UAV", "unmanned aerial vehicle design"],
    "unmanned-aircraft-systems/uas-propulsion": [
        "UAV propulsion", "drone propulsion", "electric UAV", "hybrid UAV"],
    "unmanned-aircraft-systems/autonomous-flight-control": [
        "autonomous flight", "UAV flight control", "autonomous UAV"],
    "unmanned-aircraft-systems/gnss-denied-navigation": [
        "GNSS denied navigation", "GPS denied navigation", "GNSS-denied UAV", "GPS-denied UAV"],
    "unmanned-aircraft-systems/detect-and-avoid": [
        "detect and avoid UAV", "UAV collision avoidance", "drone obstacle avoidance"],
    "unmanned-aircraft-systems/drone-swarms": [
        "drone swarms", "UAV swarms", "multi-UAV systems"],
    "unmanned-aircraft-systems/uas-communications": [
        "UAV communication", "drone communication", "UAV command and control"],
    "unmanned-aircraft-systems/uas-certification": [
        "UAV certification", "unmanned aircraft safety", "drone airspace integration"],
}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".temporary")
    temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _post(base: str, route: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base + route, data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code} {route}: " +
                           error.read(1000).decode("utf-8", "replace")) from error


def _get(base: str, route: str) -> dict:
    with urllib.request.urlopen(base + route, timeout=30) as response:
        return json.load(response)


def _operation(state: dict, step: str, output: Path) -> str:
    operations = state.setdefault("operation_ids", {})
    if step not in operations:
        operations[step] = str(uuid.uuid4())
        _save(output, state)
    return operations[step]


def _poll(base: str, state: dict, output: Path, job_id: str,
          stage: str, deadline: float) -> dict | None:
    last_status = None
    while time.monotonic() < deadline:
        job = _get(base, "/jobs/" + job_id)
        status = job["status"]
        if status != last_status:
            state.setdefault("events", []).append({
                "at": datetime.now(timezone.utc).isoformat(),
                "stage": stage, "job_id": job_id, "status": status})
            _save(output, state)
            last_status = status
        if status == "succeeded":
            return job
        if status in {"failed", "cancelled"}:
            state["error"] = job.get("error") or status
            _save(output, state)
            raise RuntimeError(f"{stage} job {status}: {state['error']}")
        time.sleep(5)
    state["status"] = "deadline_exceeded_job_continues"
    state["pending_job_id"] = job_id
    state["pending_stage"] = stage
    _save(output, state)
    return None


def run(*, base: str, output: Path, resume: bool, limit_seconds: int) -> dict:
    if not 60 <= limit_seconds <= 1200:
        raise ValueError("Limit must be 60–1200 seconds")
    if output.exists():
        if not resume:
            raise FileExistsError("Existing run requires --resume")
        state = json.loads(output.read_text(encoding="utf-8"))
        if state.get("version") != "scout-end-to-end-bas-smoke-v1":
            raise ValueError("Unknown run state")
        if state.get("status") == "succeeded":
            return state
    else:
        state = {"version": "scout-end-to-end-bas-smoke-v1",
                 "query_ru": "Беспилотные авиационные системы",
                 "started_at": datetime.now(timezone.utc).isoformat(),
                 "limit_seconds": limit_seconds,
                 "policy": {"scout_ui_equivalent_branch_hints": True,
                            "expert_validation_not_required": True,
                            "one_query_not_latency_distribution": True,
                            "new_jobs_are_real_project_jobs": True},
                 "events": [], "status": "started"}
        _save(output, state)
    started_epoch = datetime.fromisoformat(state["started_at"]).timestamp()
    deadline = time.monotonic() + max(0, limit_seconds -
                                      (time.time() - started_epoch))
    if time.monotonic() >= deadline:
        state["status"] = "deadline_exceeded_job_continues"
        _save(output, state)
        return state
    query = state["query_ru"]
    if "preview_sha256" not in state:
        preview = _post(base, "/query-plan/preview",
                        {"query": query, "max_suggestions": 12})
        suggestions = preview.get("suggestions") or []
        expected = set(BRANCH_PHRASES)
        found = {item["suggestion_id"] for item in suggestions}
        if not expected <= found:
            raise ValueError("BAS preview changed; do not silently benchmark another plan")
        state["preview_sha256"] = preview["plan_payload_sha256"]
        state["suggestion_ids"] = [item["suggestion_id"] for item in suggestions]
        state["suggestions"] = {item["suggestion_id"]: item for item in suggestions}
        _save(output, state)
    if "plan_id" not in state:
        operation = _operation(state, "approve", output)
        approved = _post(base, "/query-plan/approve", {
            "query": query, "max_suggestions": 12,
            "preview_payload_sha256": state["preview_sha256"],
            "selected_branch_ids": state["suggestion_ids"],
            "approved_by": "goal-slo-smoke", "operation_id": operation})
        state["plan_id"] = approved["plan_id"]
        state["approved_branches"] = approved["approved_plan"]["branches"]
        _save(output, state)
    if "compilation_id" not in state:
        operation = _operation(state, "compile", output)
        specs = []
        for branch in state["approved_branches"]:
            identifier = branch["branch_id"]
            phrases = ([branch["query"]] if identifier == "original-query" else
                       BRANCH_PHRASES[identifier])
            specs.append({"branch_id": identifier, "included_phrases": phrases,
                          "excluded_phrases": []})
        compilation = _post(base, "/query-plans/" + state["plan_id"] + "/compile", {
            "branch_specs": specs, "compiled_by": "goal-slo-smoke",
            "operation_id": operation})
        state["compilation_id"] = compilation["compilation_id"]
        _save(output, state)
    if "discovery_job_id" not in state:
        operation = _operation(state, "discovery", output)
        discovery = _post(base, "/query-plans/" + state["plan_id"] +
                          "/jobs/discovery", {
            "requested_by": "goal-slo-smoke", "date_from": "2021-09-01",
            "as_of_date": "2026-09-01", "limit_per_source": 25,
            "max_results": 300, "compiled_query_plan_id": state["compilation_id"],
            "openalex_collection_mode": "live_with_cache_fallback",
            "operation_id": operation})
        state["discovery_job_id"] = discovery["job_id"]
        _save(output, state)
    discovery_job = _poll(base, state, output, state["discovery_job_id"],
                          "collect", deadline)
    if discovery_job is None:
        return state
    if "analysis_job_id" not in state:
        operation = _operation(state, "analysis", output)
        analysis = _post(base, "/jobs/" + state["discovery_job_id"] +
                         "/full-analysis", {
            "requested_by": "goal-slo-smoke", "max_records": 10000,
            "top_n": 100, "operation_id": operation})
        state["analysis_job_id"] = analysis["analysis_job"]["job_id"]
        _save(output, state)
    result = _poll(base, state, output, state["analysis_job_id"],
                   "analyze", deadline)
    if result is None:
        return state
    state["status"] = "succeeded"
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    state["elapsed_seconds"] = time.time() - started_epoch
    state["within_20_minutes"] = state["elapsed_seconds"] <= 1200
    state["mission_id"] = result.get("mission_id")
    state["score_run_id"] = (result.get("result") or {}).get("runs", {}).get("score")
    state.pop("pending_job_id", None)
    state.pop("pending_stage", None)
    _save(output, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit-seconds", type=int, default=1200)
    args = parser.parse_args()
    result = run(base=args.base, output=args.output, resume=args.resume,
                 limit_seconds=args.limit_seconds)
    print(json.dumps({"status": result["status"],
                      "discovery_job_id": result.get("discovery_job_id"),
                      "analysis_job_id": result.get("analysis_job_id"),
                      "elapsed_seconds": result.get("elapsed_seconds"),
                      "score_run_id": result.get("score_run_id")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
