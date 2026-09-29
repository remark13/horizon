"""Append-only exact-phrase compilation of an approved transparent plan."""
from __future__ import annotations

import hashlib
import json
import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION
from saia.query_plan_store import verify_payload as verify_approved_plan


VERSION = "compiled-exact-phrase-query-plan-0.4.37"
CONCEPT_VERSION = "compiled-concept-group-query-plan-0.4.53"
LEGACY_MATCHING = "any_exact_included_phrase_and_no_exact_excluded_phrase"
CONCEPT_MATCHING = "(any_exact_included_phrase_or_all_concept_groups)_and_no_exact_excluded_phrase"


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(str(value))) if value is not None else str(uuid.uuid4())
    except ValueError:
        raise ValueError("Ключ компиляции должен быть UUID.") from None


def _phrase_list(values: list[str], label: str, *, required: bool) -> list[str]:
    if not isinstance(values, list) or (required and not values) or len(values) > 10:
        requirement = "от 1 до 10" if required else "не более 10"
        raise ValueError(f"Для каждой ветви укажите {requirement} {label}.")
    cleaned = [" ".join(str(value).split()) for value in values]
    if any(not 2 <= len(value) <= 160 for value in cleaned):
        raise ValueError(f"Каждая {label} должна содержать от 2 до 160 символов.")
    if len({value.casefold() for value in cleaned}) != len(cleaned):
        raise ValueError(f"Одинаковые {label} не должны повторяться.")
    return cleaned


def _concept_groups(values: list[list[str]]) -> list[list[str]]:
    if values is None:
        return []
    if not isinstance(values, list) or (values and not 2 <= len(values) <= 5):
        raise ValueError("Составной поиск требует от 2 до 5 обязательных понятий.")
    if any(not isinstance(group, list) or not 1 <= len(group) <= 3 for group in values):
        raise ValueError("В одном понятии допускается от 1 до 3 близких формулировок.")
    groups = [_phrase_list(group, "синонимы понятия", required=True) for group in values]
    flattened = [phrase.casefold() for group in groups for phrase in group]
    if len(flattened) != len(set(flattened)):
        raise ValueError("Одна фраза не должна повторяться в разных понятиях.")
    return groups


def validate_compilation(
    approved: dict,
    branch_specs: list[dict],
    compiled_by: str,
    compilation_id: str,
) -> dict:
    approved = verify_approved_plan(approved)
    compiled_by = " ".join(compiled_by.split())
    if not 1 <= len(compiled_by) <= 120:
        raise ValueError("Идентификатор составителя должен содержать от 1 до 120 символов.")
    expected = [branch["branch_id"] for branch in approved["branches"]]
    observed = [str(spec.get("branch_id") or "") for spec in branch_specs]
    if len(observed) != len(set(observed)):
        raise ValueError("Одна и та же ветвь не должна компилироваться дважды.")
    if set(observed) != set(expected) or len(observed) != len(expected):
        raise ValueError("Нужно явно задать фразы для всех и только подтверждённых ветвей.")
    by_id = {spec["branch_id"]: spec for spec in branch_specs}
    compiled = []
    any_concepts = False
    for branch in approved["branches"]:
        spec = by_id[branch["branch_id"]]
        included = _phrase_list(spec.get("included_phrases"), "включаемые фразы", required=True)
        excluded = _phrase_list(spec.get("excluded_phrases") or [], "исключаемые фразы", required=False)
        groups = _concept_groups(spec.get("concept_groups") or [])
        any_concepts = any_concepts or bool(groups)
        if (set(value.casefold() for value in [*included, *(item for group in groups for item in group)])
                & set(value.casefold() for value in excluded)):
            raise ValueError("Одна фраза не может быть одновременно включаемой и исключаемой.")
        item = {
            "branch_id": branch["branch_id"],
            "source_query": branch["query"],
            "included_phrases": included,
            "excluded_phrases": excluded,
            "matching": CONCEPT_MATCHING if groups else LEGACY_MATCHING,
            "matching_version": ORTHOGRAPHIC_MATCHING_VERSION,
        }
        if groups:
            item["concept_groups"] = groups
        compiled.append(item)
    payload = {
        "version": CONCEPT_VERSION if any_concepts else VERSION,
        "compilation_id": compilation_id,
        "approved_query_plan_id": approved["plan_id"],
        "approved_plan_payload_sha256": approved["payload_sha256"],
        "compiled_by": compiled_by,
        "branch_specs": compiled,
        "complete": True,
        "append_only": True,
        "automatic_translation": False,
        "exact_phrase_semantics": True,
        "scientific_result": False,
        "execution_started": False,
        "interpretation": (
            "Это явно подтверждённые правила извлечения из локального корпуса, "
            "а не оценка релевантности или слабого сигнала."
        ),
    }
    payload["payload_sha256"] = _digest(payload)
    return payload


def verify_payload(payload: dict) -> dict:
    value = dict(payload)
    checksum = value.pop("payload_sha256", None)
    if not isinstance(checksum, str) or checksum != _digest(value):
        raise ValueError("Контрольная сумма скомпилированного плана не совпала.")
    if (
        payload.get("version") not in {VERSION, CONCEPT_VERSION}
        or payload.get("complete") is not True
        or payload.get("append_only") is not True
        or payload.get("automatic_translation") is not False
        or payload.get("exact_phrase_semantics") is not True
        or payload.get("scientific_result") is not False
        or payload.get("execution_started") is not False
    ):
        raise ValueError("Скомпилированный план не прошёл проверку политики.")
    if payload["version"] == VERSION:
        if any(branch.get("concept_groups") or branch.get("matching") != LEGACY_MATCHING
               for branch in payload.get("branch_specs") or []):
            raise ValueError("Старая версия плана не допускает составные понятия.")
    else:
        if not any(branch.get("concept_groups") for branch in payload.get("branch_specs") or []):
            raise ValueError("Составная версия плана должна содержать понятия.")
        for branch in payload["branch_specs"]:
            groups = _concept_groups(branch.get("concept_groups") or [])
            if branch.get("matching") != (CONCEPT_MATCHING if groups else LEGACY_MATCHING):
                raise ValueError("Способ сопоставления не соответствует составу ветви.")
    return payload


def compile_plan(
    approved_query_plan_id: str,
    branch_specs: list[dict],
    compiled_by: str,
    operation_id: str | None = None,
) -> dict:
    plan_id, compilation_id = _uuid(approved_query_plan_id), _uuid(operation_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT payload FROM approved_query_plan WHERE plan_id=%s", (plan_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Подтверждённый поисковый план не найден.")
        payload = validate_compilation(row[0], branch_specs, compiled_by, compilation_id)
        expected = (
            plan_id, payload["approved_plan_payload_sha256"], payload["compiled_by"],
            payload["branch_specs"], payload["payload_sha256"], payload,
        )
        cur.execute(
            "INSERT INTO compiled_query_plan "
            "(compilation_id,approved_query_plan_id,approved_plan_payload_sha256,"
            "compiled_by,branch_specs,payload_sha256,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING created_at",
            (compilation_id, *expected[:3], Jsonb(expected[3]), expected[4], Jsonb(expected[5])),
        )
        inserted = cur.fetchone()
        replayed = inserted is None
        if inserted:
            created = inserted[0]
        else:
            cur.execute(
                "SELECT approved_query_plan_id::text,approved_plan_payload_sha256,"
                "compiled_by,branch_specs,payload_sha256,payload,created_at "
                "FROM compiled_query_plan WHERE compilation_id=%s", (compilation_id,),
            )
            old = cur.fetchone()
            if old is None or tuple(old[:6]) != expected:
                raise ValueError(
                    "Этот ключ уже использован для другой компиляции; история не изменена."
                )
            payload, created = verify_payload(old[5]), old[6]
    return {
        "compilation_id": compilation_id,
        "created_at": created.isoformat(),
        "replayed": replayed,
        "compiled_plan": payload,
        "execution_started": False,
    }


def read(compilation_id: str) -> dict:
    identifier = _uuid(compilation_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT approved_query_plan_id::text,approved_plan_payload_sha256,compiled_by,"
            "branch_specs,payload_sha256,payload,created_at FROM compiled_query_plan "
            "WHERE compilation_id=%s", (identifier,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError("Скомпилированный поисковый план не найден.")
    payload = verify_payload(row[5])
    expected = (
        payload["approved_query_plan_id"], payload["approved_plan_payload_sha256"],
        payload["compiled_by"], payload["branch_specs"], payload["payload_sha256"],
    )
    if tuple(row[:5]) != expected or payload["compilation_id"] != identifier:
        raise ValueError("Колонки истории не совпали со скомпилированным планом.")
    return {"compilation_id": identifier, "created_at": row[6].isoformat(),
            "compiled_plan": payload, "execution_started": False}
