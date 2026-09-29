from __future__ import annotations

import copy
import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
if not os.environ.get("SAIA_DATABASE_URL"):
    pytest.skip("SAIA_DATABASE_URL не задан", allow_module_level=True)

from psycopg.types.json import Jsonb  # noqa: E402

from saia import db, retrieval_review_store  # noqa: E402
from saia.retrieval_relevance_review import current_packet, current_template  # noqa: E402


def completed(reviewer: str, relevance: str = "relevant", role: str = "central") -> dict:
    value = copy.deepcopy(current_template())
    value["reviewer_id"] = reviewer
    value["independent_review_declared"] = True
    value["hidden_fields_not_seen_declared"] = True
    for row in value["annotations"]:
        row["answers"] = {"topical_relevance": relevance, "evidence_role": role}
        row["rationale"] = "Synthetic database fixture; not an expert assessment."
        row["sources"] = []
    return value


def cleanup(prefix: str) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "ALTER TABLE retrieval_review_submission "
                "DISABLE TRIGGER retrieval_review_submission_no_change"
            )
            cur.execute(
                "DELETE FROM retrieval_review_submission WHERE reviewer_id LIKE %s",
                (prefix + "%",),
            )
        finally:
            cur.execute(
                "ALTER TABLE retrieval_review_submission "
                "ENABLE TRIGGER retrieval_review_submission_no_change"
            )


def test_retrieval_store_is_append_only_idempotent_and_version_scoped():
    marker = "synthetic-retrieval-store-" + uuid.uuid4().hex
    cleanup(marker)
    try:
        operation = str(uuid.uuid4())
        left_raw = completed(marker + "-A")
        first = retrieval_review_store.record(left_raw, operation)
        replay = retrieval_review_store.record(left_raw, operation)
        duplicate = retrieval_review_store.record(left_raw, str(uuid.uuid4()))
        assert first["submission_id"] == replay["submission_id"] == duplicate["submission_id"]
        assert first["replayed"] is False and replay["replayed"] is True
        assert duplicate["deduplicated_by_content"] is True
        assert first["precision_available"] is False

        changed = copy.deepcopy(left_raw)
        changed["annotations"][0]["answers"]["topical_relevance"] = "not_relevant"
        with pytest.raises(ValueError, match="другой retrieval-анкеты"):
            retrieval_review_store.record(changed, operation)
        partial = copy.deepcopy(left_raw)
        partial["annotations"].pop()
        with pytest.raises(ValueError, match="только полные"):
            retrieval_review_store.record(partial)

        right = retrieval_review_store.record(
            completed(marker + "-B", "not_relevant", "incidental"), str(uuid.uuid4())
        )
        comparison = retrieval_review_store.compare(
            first["submission_id"], right["submission_id"]
        )
        assert comparison["consensus_created"] is False
        assert comparison["precision_available"] is False
        assert comparison["adjudication_required"] is True
        assert len(comparison["disagreements"]) == 198
        assert comparison["stored_submission_ids"] == [
            first["submission_id"], right["submission_id"]]
        with pytest.raises(ValueError, match="две разные"):
            retrieval_review_store.compare(first["submission_id"], first["submission_id"])

        saved = retrieval_review_store.read(first["submission_id"])
        assert saved["validated_submission"] == first["validated_submission"]
        history = retrieval_review_store.history(500)
        assert {first["submission_id"], right["submission_id"]} <= {
            row["submission_id"] for row in history["submissions"]}

        with db.connect() as conn, conn.cursor() as cur:
            for sql in (
                "UPDATE retrieval_review_submission SET reviewer_id=reviewer_id "
                "WHERE submission_id=%s",
                "DELETE FROM retrieval_review_submission WHERE submission_id=%s",
            ):
                with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                    with conn.transaction():
                        cur.execute(sql, (first["submission_id"],))
            packet = current_packet()
            with pytest.raises(psycopg.errors.RaiseException, match="must agree"):
                with conn.transaction():
                    cur.execute(
                        "INSERT INTO retrieval_review_submission "
                        "(submission_id,package_id,packet_payload_sha256,reviewer_id,"
                        "submission_payload_sha256,payload) VALUES (%s,%s,%s,%s,%s,%s)",
                        (
                            str(uuid.uuid4()), packet["package_id"],
                            packet["packet_payload_sha256"], marker + "-invalid",
                            "0" * 64, Jsonb({"complete": True}),
                        ),
                    )
    finally:
        cleanup(marker)
