from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
if not os.environ.get("SAIA_DATABASE_URL"):
    pytest.skip("SAIA_DATABASE_URL не задан", allow_module_level=True)

from saia import db  # noqa: E402
from saia import balanced_retrieval_adjudication as adjudication  # noqa: E402
from saia import balanced_retrieval_review_store as review_store  # noqa: E402
from saia import universal_materialization  # noqa: E402
from saia.balanced_retrieval_review import build_packet  # noqa: E402
from saia.full_analysis import ensure_arxiv_source_profile, job_payload  # noqa: E402


def job() -> dict:
    value = {
        "job_id": "00000000-0000-0000-0000-000000000121",
        "approved_query_plan_id": "00000000-0000-0000-0000-000000000122",
        "job_kind": "approved_balanced_discovery", "status": "succeeded",
        "result_role": "balanced_corpus_candidate_not_signals",
        "result_sha256": None,
        "payload": {"branches": [
            {"branch_id": "original-query", "query": "тканевая инженерия"},
        ], "date_from": "2020-01-01", "as_of_date": "2026-01-01",
            "compiled_query_plan_id": "00000000-0000-0000-0000-000000000123",
            "compiled_plan_payload_sha256": "c" * 64,
            "compiled_branch_specs": [{
                "branch_id": "original-query",
                "included_phrases": ["tissue engineering"],
                "excluded_phrases": [],
            }]},
        "result": {
            "result_role": "balanced_corpus_candidate_not_signals",
            "weak_signal_assessment_performed": False,
            "works": [{
                "canonical_key": "doi:10.1/store-example", "title": "Example",
                "abstract": "Primary result", "published_at": "2024-01-01",
                "urls": ["https://doi.org/10.1/store-example"],
                "branch_provenance": ["original-query"],
                "source_provenance": ["openalex"],
            }],
        },
    }
    value["result_sha256"] = hashlib.sha256(json.dumps(
        value["result"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()
    return value


def completed(template: dict) -> dict:
    value = copy.deepcopy(template)
    value["reviewer_id"] = "reviewer"
    value["independent_review_declared"] = True
    value["hidden_fields_not_seen_declared"] = True
    for row in value["annotations"]:
        row["answers"] = {"topical_relevance": "relevant", "evidence_role": "central"}
        row["rationale"] = "The publication directly addresses the target topic."
    return value


def cleanup(marker: str) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "ALTER TABLE retrieval_review_adjudication "
            "DISABLE TRIGGER retrieval_review_adjudication_no_change"
        )
        cur.execute(
            "DELETE FROM retrieval_review_adjudication WHERE adjudicator_id LIKE %s",
            (marker + "%",),
        )
        cur.execute(
            "ALTER TABLE retrieval_review_adjudication "
            "ENABLE TRIGGER retrieval_review_adjudication_no_change"
        )
        cur.execute(
            "ALTER TABLE retrieval_review_submission "
            "DISABLE TRIGGER retrieval_review_submission_no_change"
        )
        cur.execute(
            "DELETE FROM retrieval_review_submission WHERE reviewer_id LIKE %s",
            (marker + "%",),
        )
        cur.execute(
            "ALTER TABLE retrieval_review_submission "
            "ENABLE TRIGGER retrieval_review_submission_no_change"
        )


def test_dynamic_reviews_and_adjudication_are_append_only(monkeypatch):
    marker = "synthetic-balanced-adjudication-" + uuid.uuid4().hex
    packet, template = build_packet(job())
    monkeypatch.setattr(review_store, "from_job_id", lambda value: (packet, template))
    monkeypatch.setattr(adjudication, "from_job_id", lambda value: (packet, template))
    monkeypatch.setattr(universal_materialization, "read_job", lambda value: job())
    cleanup(marker)
    try:
        left_raw = completed(template)
        left_raw["reviewer_id"] = marker + "-A"
        right_raw = copy.deepcopy(left_raw)
        right_raw["reviewer_id"] = marker + "-B"
        right_raw["annotations"][0]["answers"]["topical_relevance"] = "not_relevant"
        left = review_store.record(job()["job_id"], left_raw, str(uuid.uuid4()))
        right = review_store.record(job()["job_id"], right_raw, str(uuid.uuid4()))
        decisions = [{
            "item_id": item["item_id"],
            "topical_relevance": "relevant",
            "rationale": "The primary publication directly addresses this target topic.",
            "sources": [],
        } for item in packet["items"]]
        operation_id = str(uuid.uuid4())
        saved = adjudication.record(
            job()["job_id"], left["submission_id"], right["submission_id"],
            marker + "-C", decisions, operation_id,
        )
        replay = adjudication.record(
            job()["job_id"], left["submission_id"], right["submission_id"],
            marker + "-C", decisions, operation_id,
        )
        assert saved["adjudication_id"] == replay["adjudication_id"]
        assert saved["replayed"] is False and replay["replayed"] is True
        assert saved["adjudication"]["precision_available"] is True
        assert saved["adjudication"]["weak_signal_accuracy_measured"] is False
        history = adjudication.history(job()["job_id"])
        assert saved["adjudication_id"] in {
            row["adjudication_id"] for row in history["adjudications"]
        }
        materialized = universal_materialization.materialize(
            job()["job_id"], saved["adjudication_id"], marker + "-D",
            acknowledge_arxiv_only=True, acknowledge_phrase_union=True,
        )
        replayed = universal_materialization.materialize(
            job()["job_id"], saved["adjudication_id"], marker + "-D",
            acknowledge_arxiv_only=True, acknowledge_phrase_union=True,
        )
        assert materialized["replayed"] is False and replayed["replayed"] is True
        assert materialized["mission_id"] == replayed["mission_id"]
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload->'collection_profile'->'provenance'->>'retrieval_adjudication_id' "
                "FROM query_version WHERE query_version_id=%s",
                (materialized["query_version_id"],),
            )
            assert cur.fetchone()[0] == saved["adjudication_id"]
            execution_id, execution_profile, reused = ensure_arxiv_source_profile(
                cur, materialized["mission_id"], materialized["query_version_id"],
                marker + "-D",
            )
            assert reused is True
            assert execution_id == materialized["query_version_id"]
            request = job_payload(
                materialized["mission_id"], materialized["query_version_id"],
                execution_id, execution_profile, 1000, 15,
            )
            assert request["source_scope"] == ["arxiv"]
            assert request["pipeline"][-2:] == ["score", "triage"]
        with db.connect() as conn, conn.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                with conn.transaction():
                    cur.execute(
                        "DELETE FROM retrieval_review_adjudication WHERE adjudication_id=%s",
                        (saved["adjudication_id"],),
                    )
    finally:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mission WHERE created_by=%s", (marker + "-D",))
        cleanup(marker)
