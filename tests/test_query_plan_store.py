from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
if not os.environ.get("SAIA_DATABASE_URL"):
    pytest.skip("SAIA_DATABASE_URL не задан", allow_module_level=True)

from psycopg.types.json import Jsonb  # noqa: E402

from saia import db, query_plan_store  # noqa: E402
from saia.query_planning import preview  # noqa: E402


def cleanup(plan_ids: list[str]) -> None:
    if not plan_ids:
        return
    with db.connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "ALTER TABLE analysis_job_event DISABLE TRIGGER analysis_job_event_no_change"
            )
            cur.execute(
                "DELETE FROM analysis_job_event WHERE job_id IN "
                "(SELECT job_id FROM analysis_job WHERE approved_query_plan_id=ANY(%s))",
                (plan_ids,),
            )
        finally:
            cur.execute(
                "ALTER TABLE analysis_job_event ENABLE TRIGGER analysis_job_event_no_change"
            )
        try:
            cur.execute("ALTER TABLE analysis_job DISABLE TRIGGER analysis_job_state_guard")
            cur.execute(
                "DELETE FROM analysis_job WHERE approved_query_plan_id=ANY(%s)",
                (plan_ids,),
            )
        finally:
            cur.execute("ALTER TABLE analysis_job ENABLE TRIGGER analysis_job_state_guard")
        try:
            cur.execute(
                "ALTER TABLE approved_query_plan DISABLE TRIGGER approved_query_plan_no_change"
            )
            cur.execute("DELETE FROM approved_query_plan WHERE plan_id=ANY(%s)", (plan_ids,))
        finally:
            cur.execute(
                "ALTER TABLE approved_query_plan ENABLE TRIGGER approved_query_plan_no_change"
            )


def test_approved_plan_store_is_append_only_idempotent_and_guarded():
    operation = str(uuid.uuid4())
    other = str(uuid.uuid4())
    cleanup([operation, other])
    try:
        shown = preview("новые материалы", 12)
        branch = shown["suggestions"][0]["suggestion_id"]
        args = (
            "новые материалы", 12, shown["plan_payload_sha256"],
            [branch], "synthetic-plan-reviewer",
        )
        first = query_plan_store.approve(*args, operation)
        replay = query_plan_store.approve(*args, operation)
        assert first["plan_id"] == replay["plan_id"] == operation
        assert first["replayed"] is False and replay["replayed"] is True
        assert first["execution_started"] is False
        assert query_plan_store.read(operation)["approved_plan"] == first["approved_plan"]

        changed = list(args)
        changed[3] = []
        with pytest.raises(ValueError, match="другого поискового плана"):
            query_plan_store.approve(*changed, operation)

        history = query_plan_store.history(500)
        assert operation in {item["plan_id"] for item in history["plans"]}

        with db.connect() as conn, conn.cursor() as cur:
            for sql in (
                "UPDATE approved_query_plan SET approved_by=approved_by WHERE plan_id=%s",
                "DELETE FROM approved_query_plan WHERE plan_id=%s",
            ):
                with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                    with conn.transaction():
                        cur.execute(sql, (operation,))
            invalid = {
                "plan_id": other, "original_query": "новые материалы",
                "max_suggestions": 12, "preview_payload_sha256": "0" * 64,
                "approved_by": "synthetic-plan-reviewer", "selected_branch_ids": [],
                "payload_sha256": "1" * 64, "complete": True, "append_only": True,
                "original_query_preserved": True, "automatic_execution": False,
                "scientific_result": False,
            }
            with pytest.raises(psycopg.errors.RaiseException, match="guarded payload"):
                with conn.transaction():
                    cur.execute(
                        "INSERT INTO approved_query_plan "
                        "(plan_id,original_query,max_suggestions,preview_payload_sha256,"
                        "approved_by,selected_branch_ids,payload_sha256,payload) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (other, "other query", 12, "0" * 64,
                         "synthetic-plan-reviewer", Jsonb([]), "1" * 64, Jsonb(invalid)),
                    )
    finally:
        cleanup([operation, other])


def test_approved_plan_can_drive_durable_balanced_collection_without_becoming_a_signal():
    from datetime import date
    from saia import jobs

    plan_id, job_operation = str(uuid.uuid4()), str(uuid.uuid4())
    cleanup([plan_id])
    try:
        shown = preview("тканевая инженерия", 12)
        branch = shown["suggestions"][0]["suggestion_id"]
        query_plan_store.approve(
            "тканевая инженерия", 12, shown["plan_payload_sha256"],
            [branch], "synthetic-job-reviewer", plan_id,
        )
        queued = jobs.enqueue_balanced_discovery(
            plan_id, "synthetic-job-reviewer", date(2020, 1, 1), date(2026, 1, 1),
            limit_per_source=3, max_results=5, operation_id=job_operation,
        )
        replay = jobs.enqueue_balanced_discovery(
            plan_id, "synthetic-job-reviewer", date(2020, 1, 1), date(2026, 1, 1),
            limit_per_source=3, max_results=5, operation_id=job_operation,
        )
        assert queued["job_id"] == replay["job_id"]
        assert queued["mission_id"] is None and queued["query_version_id"] is None
        assert queued["approved_query_plan_id"] == plan_id

        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE analysis_job SET status='running',attempt_count=1,lease_owner=%s,"
                "lease_expires_at=now()+interval '30 seconds',heartbeat_at=now(),"
                "started_at=now() WHERE job_id=%s",
                ("synthetic-worker", queued["job_id"]),
            )
        def fake_collector(query, *_args):
            return {
                "works": [{
                    "canonical_key": "title:" + query, "title": query,
                    "abstract": None, "published_at": "2025-01-01",
                    "sources": ["openalex"], "source_ids": [query],
                    "urls": [], "doi": None, "authors": [],
                }],
                "source_counts": {"openalex": 1, "arxiv": 0}, "errors": {},
            }
        finished = jobs.execute(queued["job_id"], "synthetic-worker", fake_collector)
        assert finished["status"] == "succeeded"
        assert finished["result_role"] == "balanced_corpus_candidate_not_signals"
        assert finished["result"]["scientific_score_calculated"] is False
        assert finished["result"]["weak_signal_assessment_performed"] is False
        assert len(finished["result"]["works"]) == 2
        assert jobs.query_plan_history(plan_id)["jobs"][0]["job_id"] == queued["job_id"]
    finally:
        cleanup([plan_id])
