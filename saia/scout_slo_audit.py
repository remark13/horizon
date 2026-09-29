"""Freeze observable result and limitations of one completed scout smoke."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request

from saia.controlled_collection import sha256_file


VERSION = "scout-end-to-end-result-audit-v1"


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as response:
        return json.load(response)


def audit(*, state_path: Path, base: str) -> dict:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if (state.get("version") != "scout-end-to-end-bas-smoke-v1"
            or state.get("status") != "succeeded"
            or not state.get("discovery_job_id") or not state.get("analysis_job_id")
            or not state.get("mission_id") or not state.get("score_run_id")):
        raise ValueError("Scout smoke has not completed with linked results")
    discovery = _get(base, "/jobs/" + state["discovery_job_id"])
    analysis = _get(base, "/jobs/" + state["analysis_job_id"])
    triage = _get(base, "/triage/" + state["mission_id"] +
                  "?score_run_id=" + str(state["score_run_id"]) + "&limit=100")
    if (discovery.get("status") != "succeeded" or analysis.get("status") != "succeeded"
            or analysis.get("mission_id") != state["mission_id"]
            or triage.get("mission_id") != state["mission_id"]
            or triage.get("score_run_id") != state["score_run_id"]):
        raise ValueError("Scout result does not match completed job chain")
    queue = triage["queue"]
    statuses = Counter(row["card"]["status"] for row in queue)
    observed_growth = sum(
        row["card"].get("metrics", {}).get("observed", {}).get(
            "momentum_percentile") is not None for row in queue)
    stages = [
        {"event_type": event.get("event_type"),
         "stage": (event.get("details") or {}).get("stage")}
        for event in analysis.get("events") or []
        if str(event.get("event_type") or "").startswith("stage_")]
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "state_sha256": sha256_file(state_path),
            "query_ru": state["query_ru"],
            "from_user_request_to_completed_cards_seconds": state["elapsed_seconds"],
            "within_20_minutes_this_one_run": state["within_20_minutes"],
            "discovery_job_id": state["discovery_job_id"],
            "analysis_job_id": state["analysis_job_id"],
            "mission_id": state["mission_id"], "score_run_id": state["score_run_id"],
            "discovery_works_in_bounded_result": len((discovery.get("result") or {}).get("works") or []),
            "automatic_cards": triage["counts"]["all_cards"],
            "cards_in_queue": len(queue),
            "card_statuses": dict(sorted(statuses.items())),
            "cards_with_known_momentum_percentile": observed_growth,
            "parent_corpus_context_present": triage.get("parent_corpus_context") is not None,
            "analysis_stage_events": stages,
            "top_15_labels_and_statuses": [
                {"rank": row["rank"], "label": row["card"]["label"],
                 "status": row["card"]["status"]} for row in queue[:15]],
            "limits": {"one_query_not_p50_or_p95": True,
                       "automatic_candidates_not_confirmed_weak_signals": True,
                       "bounded_result_not_full_field_series": True,
                       "customer_100_not_used_as_gold": True,
                       "quality_not_independently_adjudicated": True}}
