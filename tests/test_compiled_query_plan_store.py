from __future__ import annotations

import copy
import os
import uuid

import pytest

from saia.compiled_query_plan_store import (CONCEPT_MATCHING, CONCEPT_VERSION,
                                            validate_compilation, verify_payload)
from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.query_plan_store import validate_approval
from saia.query_planning import preview


def approved_plan():
    shown = preview("тканевая инженерия", 12)
    branch = shown["suggestions"][0]["suggestion_id"]
    return validate_approval(
        "тканевая инженерия", 12, shown["plan_payload_sha256"], [branch],
        "analyst", str(uuid.uuid4()),
    )


def specs():
    return [
        {
            "branch_id": "original-query",
            "included_phrases": ["тканевая инженерия"],
            "excluded_phrases": [],
        },
        {
            "branch_id": "health-preservation/tissue-engineering",
            "included_phrases": ["tissue engineering", "3D bioprinting"],
            "excluded_phrases": ["architecture"],
        },
    ]


def test_compilation_requires_explicit_phrases_for_every_approved_branch():
    payload = validate_compilation(
        approved_plan(), specs(), "analyst", str(uuid.uuid4()))
    assert payload["automatic_translation"] is False
    assert payload["execution_started"] is False
    assert payload["branch_specs"][1]["included_phrases"] == [
        "tissue engineering", "3D bioprinting"
    ]
    assert payload["branch_specs"][1]["matching_version"] == ORTHOGRAPHIC_MATCHING_VERSION
    assert verify_payload(payload) == payload

    with pytest.raises(ValueError, match="всех и только"):
        validate_compilation(
            approved_plan(), specs()[:1], "analyst", str(uuid.uuid4()))
    invented = copy.deepcopy(specs())
    invented[1]["branch_id"] = "invented"
    with pytest.raises(ValueError, match="всех и только"):
        validate_compilation(
            approved_plan(), invented, "analyst", str(uuid.uuid4()))


def test_compilation_rejects_duplicate_and_contradictory_phrases():
    duplicate = copy.deepcopy(specs())
    duplicate[0]["included_phrases"] = ["Tissue Engineering", "tissue engineering"]
    with pytest.raises(ValueError, match="не должны повторяться"):
        validate_compilation(
            approved_plan(), duplicate, "analyst", str(uuid.uuid4()))
    contradictory = copy.deepcopy(specs())
    contradictory[0]["excluded_phrases"] = ["тканевая инженерия"]
    with pytest.raises(ValueError, match="одновременно"):
        validate_compilation(
            approved_plan(), contradictory, "analyst", str(uuid.uuid4()))


def test_compilation_can_add_explicit_required_concepts_without_changing_legacy_or():
    compound = copy.deepcopy(specs())
    compound[0]["concept_groups"] = [
        ["neuromorphic chips", "neuromorphic processors"],
        ["edge devices", "on-device inference"],
    ]
    payload = validate_compilation(approved_plan(), compound, "analyst", str(uuid.uuid4()))
    assert payload["version"] == CONCEPT_VERSION
    assert payload["automatic_translation"] is False
    assert payload["branch_specs"][0]["included_phrases"] == ["тканевая инженерия"]
    assert payload["branch_specs"][0]["matching"] == CONCEPT_MATCHING
    assert payload["branch_specs"][1]["matching"] != CONCEPT_MATCHING
    assert verify_payload(payload) == payload

    bad = copy.deepcopy(compound)
    bad[0]["concept_groups"] = [["neuromorphic chips"]]
    with pytest.raises(ValueError, match="2 до 5"):
        validate_compilation(approved_plan(), bad, "analyst", str(uuid.uuid4()))
    bad = copy.deepcopy(compound)
    bad[0]["concept_groups"] = [["same"], ["same"]]
    with pytest.raises(ValueError, match="повторяться"):
        validate_compilation(approved_plan(), bad, "analyst", str(uuid.uuid4()))


psycopg = pytest.importorskip("psycopg")
if not os.environ.get("SAIA_DATABASE_URL"):
    pytest.skip("SAIA_DATABASE_URL не задан", allow_module_level=True)

from saia import compiled_query_plan_store, db, query_plan_store  # noqa: E402


def cleanup(plan_id: str, compilation_ids: list[str]) -> None:
    with db.connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "ALTER TABLE compiled_query_plan DISABLE TRIGGER compiled_query_plan_no_change"
            )
            cur.execute(
                "DELETE FROM compiled_query_plan WHERE compilation_id=ANY(%s)",
                (compilation_ids,),
            )
        finally:
            cur.execute(
                "ALTER TABLE compiled_query_plan ENABLE TRIGGER compiled_query_plan_no_change"
            )
        try:
            cur.execute(
                "ALTER TABLE approved_query_plan DISABLE TRIGGER approved_query_plan_no_change"
            )
            cur.execute("DELETE FROM approved_query_plan WHERE plan_id=%s", (plan_id,))
        finally:
            cur.execute(
                "ALTER TABLE approved_query_plan ENABLE TRIGGER approved_query_plan_no_change"
            )


def test_compilation_store_is_idempotent_and_append_only():
    plan_id, compilation_id = str(uuid.uuid4()), str(uuid.uuid4())
    cleanup(plan_id, [compilation_id])
    try:
        shown = preview("тканевая инженерия", 12)
        branch = shown["suggestions"][0]["suggestion_id"]
        query_plan_store.approve(
            "тканевая инженерия", 12, shown["plan_payload_sha256"], [branch],
            "synthetic-compiler", plan_id,
        )
        first = compiled_query_plan_store.compile_plan(
            plan_id, specs(), "synthetic-compiler", compilation_id)
        replay = compiled_query_plan_store.compile_plan(
            plan_id, specs(), "synthetic-compiler", compilation_id)
        assert first["compilation_id"] == replay["compilation_id"] == compilation_id
        assert first["replayed"] is False and replay["replayed"] is True
        assert compiled_query_plan_store.read(compilation_id)["compiled_plan"] == first["compiled_plan"]
        changed = copy.deepcopy(specs())
        changed[1]["included_phrases"] = ["regenerative medicine"]
        with pytest.raises(ValueError, match="другой компиляции"):
            compiled_query_plan_store.compile_plan(
                plan_id, changed, "synthetic-compiler", compilation_id)
        with db.connect() as conn, conn.cursor() as cur:
            for sql in (
                "UPDATE compiled_query_plan SET compiled_by=compiled_by WHERE compilation_id=%s",
                "DELETE FROM compiled_query_plan WHERE compilation_id=%s",
            ):
                with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                    with conn.transaction():
                        cur.execute(sql, (compilation_id,))
    finally:
        cleanup(plan_id, [compilation_id])


def test_compound_compilation_store_roundtrip():
    plan_id, compilation_id = str(uuid.uuid4()), str(uuid.uuid4())
    cleanup(plan_id, [compilation_id])
    try:
        shown = preview("нейроморфные чипы для периферийных устройств", 12)
        query_plan_store.approve(
            shown["original_query"], 12, shown["plan_payload_sha256"], [],
            "synthetic-concept-compiler", plan_id,
        )
        spec = [{"branch_id": "original-query",
                 "included_phrases": [shown["original_query"]],
                 "concept_groups": [["neuromorphic chips"], ["edge devices"]],
                 "excluded_phrases": []}]
        stored = compiled_query_plan_store.compile_plan(
            plan_id, spec, "synthetic-concept-compiler", compilation_id)
        assert stored["compiled_plan"]["version"] == CONCEPT_VERSION
        assert compiled_query_plan_store.read(compilation_id)["compiled_plan"] == stored["compiled_plan"]
    finally:
        cleanup(plan_id, [compilation_id])
