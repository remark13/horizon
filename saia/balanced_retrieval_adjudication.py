"""Explicit adjudication and bounded retrieval precision for one job packet."""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from urllib.parse import urlsplit
import uuid

from psycopg.types.json import Jsonb

from saia import db
from saia.balanced_retrieval_review import from_job_id
from saia.balanced_retrieval_review_store import read as read_submission
from saia.query_expansion import digest


VERSION = "balanced-job-retrieval-adjudication-0.4.38"


def _uuid(value: str | None) -> str:
    try:
        return str(uuid.UUID(str(value))) if value is not None else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Ключ adjudication должен быть UUID.") from None


def _sources(values: list) -> list[str]:
    if not isinstance(values, list) or len(values) > 5:
        raise ValueError("Можно указать не более пяти источников решения.")
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Источник решения должен быть HTTPS-ссылкой.")
        link = value.strip()
        parsed = urlsplit(link)
        if (
            len(link) > 2048 or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or any(char.isspace() for char in link)
        ):
            raise ValueError("Источник решения должен быть безопасной HTTPS-ссылкой.")
        if link not in result:
            result.append(link)
    return result


def build(
    job_id: str,
    left_submission_id: str,
    right_submission_id: str,
    adjudicator_id: str,
    decisions: list[dict],
) -> dict:
    packet, _template = from_job_id(job_id)
    left_submission_id, right_submission_id = sorted(
        (_uuid(left_submission_id), _uuid(right_submission_id))
    )
    left = read_submission(job_id, left_submission_id)
    right = read_submission(job_id, right_submission_id)
    if left["submission_id"] == right["submission_id"]:
        raise ValueError("Для adjudication нужны две разные анкеты.")
    reviewers = [left["reviewer_id"], right["reviewer_id"]]
    if reviewers[0].casefold() == reviewers[1].casefold():
        raise ValueError("Для adjudication нужны два разных рецензента.")
    adjudicator = " ".join((adjudicator_id or "").split())
    if not 1 <= len(adjudicator) <= 120:
        raise ValueError("Нужен идентификатор арбитра.")
    if adjudicator.casefold() in {value.casefold() for value in reviewers}:
        raise ValueError("Арбитр должен отличаться от двух рецензентов.")

    items = {item["item_id"]: item for item in packet["items"]}
    if not isinstance(decisions, list) or len(decisions) != len(items):
        raise ValueError("Арбитр должен принять решение по каждому назначению.")
    rows, seen = [], set()
    for decision in decisions:
        item_id = decision.get("item_id")
        if item_id not in items or item_id in seen:
            raise ValueError("Неизвестный или повторный item_id adjudication.")
        seen.add(item_id)
        label = decision.get("topical_relevance")
        if label not in {"relevant", "not_relevant"}:
            raise ValueError("Итоговая релевантность должна быть бинарной.")
        rationale = decision.get("rationale")
        if not isinstance(rationale, str) or not 20 <= len(rationale.strip()) <= 5000:
            raise ValueError("Объяснение решения должно содержать от 20 до 5000 символов.")
        rows.append({
            "item_id": item_id,
            "topical_relevance": label,
            "rationale": rationale.strip(),
            "sources": _sources(decision.get("sources") or []),
        })
    if seen != set(items):
        raise ValueError("Adjudication не покрывает точный состав пакета.")
    rows.sort(key=lambda row: row["item_id"])

    counts = Counter(row["topical_relevance"] for row in rows)
    by_topic: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        topic = items[row["item_id"]]["target_topic"]
        by_topic[topic][row["topical_relevance"]] += 1
    per_topic = [{
        "target_topic": topic,
        "assignments": sum(values.values()),
        "relevant": values["relevant"],
        "not_relevant": values["not_relevant"],
        "precision_on_returned_assignments": (
            values["relevant"] / sum(values.values())
        ),
    } for topic, values in sorted(by_topic.items())]
    payload = {
        "version": VERSION,
        "source_job_id": str(job_id),
        "package_id": packet["package_id"],
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "stored_submission_ids": [left["submission_id"], right["submission_id"]],
        "reviewers": reviewers,
        "adjudicator_id": adjudicator,
        "identity": "self_declared_not_authenticated",
        "complete": True,
        "decisions": rows,
        "metrics": {
            "assignments": len(rows),
            "relevant": counts["relevant"],
            "not_relevant": counts["not_relevant"],
            "precision_on_returned_assignments": counts["relevant"] / len(rows),
            "by_target_topic": per_topic,
        },
        "precision_available": True,
        "precision_scope": "returned_work_branch_assignments_in_one_immutable_job",
        "recall_available": False,
        "weak_signal_accuracy_measured": False,
        "production_change_allowed": False,
        "interpretation": (
            "Метрика показывает долю релевантных назначений в одной сохранённой "
            "выдаче. Она не измеряет полноту поиска, качество кластеров, точность "
            "слабых сигналов или вероятность технологического успеха."
        ),
    }
    payload["adjudication_payload_sha256"] = digest(payload)
    return payload


def verify(payload: dict) -> dict:
    value = copy.deepcopy(payload)
    expected = value.pop("adjudication_payload_sha256", None)
    if (
        not expected or digest(value) != expected
        or value.get("version") != VERSION
        or value.get("complete") is not True
        or value.get("precision_available") is not True
        or value.get("recall_available") is not False
        or value.get("weak_signal_accuracy_measured") is not False
        or value.get("production_change_allowed") is not False
    ):
        raise ValueError("Сохранённое retrieval-adjudication повреждено.")
    value["adjudication_payload_sha256"] = expected
    return value


def record(
    job_id: str,
    left_submission_id: str,
    right_submission_id: str,
    adjudicator_id: str,
    decisions: list[dict],
    operation_id: str | None = None,
) -> dict:
    payload = build(
        job_id, left_submission_id, right_submission_id, adjudicator_id, decisions
    )
    adjudication_id = _uuid(operation_id)
    expected = (
        payload["package_id"], payload["packet_payload_sha256"],
        payload["stored_submission_ids"][0], payload["stored_submission_ids"][1],
        payload["adjudicator_id"], payload["adjudication_payload_sha256"], payload,
    )
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_review_adjudication "
            "(adjudication_id,package_id,packet_payload_sha256,left_submission_id,"
            "right_submission_id,adjudicator_id,adjudication_payload_sha256,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING "
            "RETURNING created_at",
            (adjudication_id, *expected[:6], Jsonb(payload)),
        )
        inserted = cur.fetchone()
        replayed = inserted is None
        if inserted:
            created = inserted[0]
        else:
            cur.execute(
                "SELECT package_id::text,packet_payload_sha256,left_submission_id::text,"
                "right_submission_id::text,adjudicator_id,adjudication_payload_sha256,"
                "payload,created_at FROM retrieval_review_adjudication "
                "WHERE adjudication_id=%s", (adjudication_id,),
            )
            old = cur.fetchone()
            if old is None or tuple(old[:7]) != expected:
                raise ValueError("Этот ключ уже использован для другого adjudication.")
            payload, created = verify(old[6]), old[7]
    return {
        "adjudication_id": adjudication_id,
        "created_at": created.isoformat(),
        "replayed": replayed,
        "adjudication": payload,
    }


def history(job_id: str, limit: int = 100) -> dict:
    if not 1 <= limit <= 500:
        raise ValueError("Лимит истории adjudication должен быть от 1 до 500.")
    packet, _template = from_job_id(job_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT adjudication_id,adjudicator_id,left_submission_id,right_submission_id,"
            "adjudication_payload_sha256,created_at,payload "
            "FROM retrieval_review_adjudication WHERE package_id=%s "
            "ORDER BY created_at DESC,adjudication_id DESC LIMIT %s",
            (packet["package_id"], limit),
        )
        rows = []
        for row in cur.fetchall():
            payload = verify(row[6])
            columns = (
                row[1], [str(row[2]), str(row[3])], row[4],
            )
            expected = (
                payload["adjudicator_id"], payload["stored_submission_ids"],
                payload["adjudication_payload_sha256"],
            )
            if columns != expected:
                raise ValueError("Колонки adjudication не совпали с payload.")
            rows.append({
                "adjudication_id": str(row[0]), "adjudicator_id": row[1],
                "stored_submission_ids": [str(row[2]), str(row[3])],
                "adjudication_payload_sha256": row[4],
                "created_at": row[5].isoformat(), "metrics": payload["metrics"],
            })
    return {
        "source_job_id": str(job_id), "package_id": packet["package_id"],
        "returned": len(rows), "adjudications": rows,
        "weak_signal_accuracy_measured": False,
        "production_change_allowed": False,
    }


def read(job_id: str, adjudication_id: str) -> dict:
    identifier = _uuid(adjudication_id)
    packet, _template = from_job_id(job_id)
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT package_id::text,packet_payload_sha256,left_submission_id::text,"
            "right_submission_id::text,adjudicator_id,adjudication_payload_sha256,"
            "payload,created_at FROM retrieval_review_adjudication "
            "WHERE adjudication_id=%s", (identifier,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("Retrieval-adjudication не найдено.")
    if row[0] != packet["package_id"] or row[1] != packet["packet_payload_sha256"]:
        raise ValueError("Retrieval-adjudication относится к другой задаче.")
    payload = verify(row[6])
    expected = (
        payload["package_id"], payload["packet_payload_sha256"],
        payload["stored_submission_ids"][0], payload["stored_submission_ids"][1],
        payload["adjudicator_id"], payload["adjudication_payload_sha256"],
    )
    if tuple(row[:6]) != expected:
        raise ValueError("Колонки retrieval-adjudication не совпали с payload.")
    return {
        "adjudication_id": identifier,
        "created_at": row[7].isoformat(),
        "adjudication": payload,
    }


def status(job_id: str) -> dict:
    from saia.balanced_retrieval_review_store import history as review_history

    reviews = review_history(job_id, 500)
    adjudications = history(job_id, 500)
    reviewers = sorted({
        row["reviewer_id"].casefold() for row in reviews["submissions"]
    })
    if adjudications["adjudications"]:
        state = "adjudicated_retrieval_only"
        next_action = (
            "Аналитик должен явно принять или отклонить качество поиска перед "
            "материализацией полного научного корпуса."
        )
    elif len(reviewers) >= 2:
        state = "awaiting_explicit_adjudication"
        next_action = "Назначьте независимого арбитра и разрешите uncertain и разногласия."
    elif len(reviewers) == 1:
        state = "awaiting_second_independent_review"
        next_action = "Нужна полная анкета второго независимого рецензента."
    else:
        state = "awaiting_first_independent_review"
        next_action = "Нужны две независимые полные анкеты релевантности."
    return {
        "source_job_id": str(job_id),
        "package_id": reviews["package_id"],
        "state": state,
        "stored_reviews": reviews["total"],
        "distinct_self_declared_reviewers": len(reviewers),
        "stored_adjudications": len(adjudications["adjudications"]),
        "retrieval_validation_complete": bool(adjudications["adjudications"]),
        "scientific_pipeline_started": False,
        "automatic_pipeline_start": False,
        "materialization_authorized": False,
        "next_action": next_action,
        "weak_signal_accuracy_measured": False,
        "production_change_allowed": False,
    }
