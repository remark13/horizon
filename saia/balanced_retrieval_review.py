"""Blinded relevance review for one immutable balanced discovery result.

The packet checks whether the publications returned for each explicitly
approved branch are actually relevant to that branch.  It deliberately does
not ask reviewers to judge emergence, market potential or a weak signal.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid

from saia.retrieval_relevance_review import (
    DYNAMIC_PACKET_VERSION,
    SUBMISSION_VERSION,
    load_policy,
)
from saia.query_expansion import digest


MAX_ASSIGNMENTS = 500


def _result_digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _verified_job(job: dict) -> dict:
    if job.get("job_kind") != "approved_balanced_discovery":
        raise ValueError("Retrieval-пакет создаётся только для многоветочного сбора.")
    if job.get("status") != "succeeded" or not isinstance(job.get("result"), dict):
        raise ValueError("Сначала дождитесь успешного завершения многоветочного сбора.")
    if job.get("result_role") != "balanced_corpus_candidate_not_signals":
        raise ValueError("Задача имеет неизвестную роль результата.")
    result = job["result"]
    if job.get("result_sha256") != _result_digest(result):
        raise ValueError("Контрольная сумма результата задачи не совпала.")
    if result.get("result_role") != job["result_role"]:
        raise ValueError("Роль сохранённого результата не совпала с задачей.")
    if result.get("weak_signal_assessment_performed") is not False:
        raise ValueError("Retrieval-пакет не должен наследовать решение о слабом сигнале.")
    return result


def _branch_labels(job: dict) -> dict[str, str]:
    labels = {}
    for branch in (job.get("payload") or {}).get("branches") or []:
        branch_id = branch.get("branch_id")
        query = " ".join(str(branch.get("query") or "").split())
        label = " ".join(str(branch.get("label_ru") or "").split())
        if branch_id and query:
            labels[branch_id] = label or query
    if not labels:
        raise ValueError("В задаче нет подтверждённых поисковых ветвей.")
    return labels


def build_packet(job: dict) -> tuple[dict, dict]:
    """Return a deterministic public packet and a matching submission template."""
    result = _verified_job(job)
    labels = _branch_labels(job)
    works = result.get("works") or []
    if not works:
        raise ValueError("В результате нет публикаций для проверки релевантности.")

    scope = {
        "version": DYNAMIC_PACKET_VERSION,
        "source_job_id": job.get("job_id"),
        "source_result_sha256": job.get("result_sha256"),
        "approved_query_plan_id": job.get("approved_query_plan_id"),
        "assignments": sorted(
            f"{branch_id}:{work.get('canonical_key')}"
            for work in works
            for branch_id in (work.get("branch_provenance") or [])
        ),
    }
    if not scope["source_job_id"] or not scope["source_result_sha256"]:
        raise ValueError("Задача не содержит неизменяемой идентичности результата.")
    if not scope["assignments"]:
        raise ValueError("Результат не содержит происхождение публикаций по ветвям.")
    if len(scope["assignments"]) > MAX_ASSIGNMENTS:
        raise ValueError(
            f"Пакет содержит больше {MAX_ASSIGNMENTS} назначений; уменьшите выдачу."
        )
    package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, digest(scope)))
    questions = copy.deepcopy(load_policy()["questions"])
    items = []
    seen = set()
    for work in works:
        canonical_key = work.get("canonical_key")
        title = " ".join(str(work.get("title") or "").split())
        if not canonical_key or not title:
            raise ValueError("Публикация без устойчивой идентичности или заголовка.")
        urls = [value for value in work.get("urls") or []
                if isinstance(value, str) and value.startswith("https://")]
        source_ids = [value for value in work.get("source_ids") or []
                      if isinstance(value, str) and value.startswith("https://")]
        url = (urls + source_ids)[0] if urls or source_ids else None
        for branch_id in work.get("branch_provenance") or []:
            if branch_id not in labels:
                raise ValueError("Публикация ссылается на неизвестную ветвь поиска.")
            item_id = "job-retrieval-item-" + hashlib.sha256(
                f"{package_id}\0{branch_id}\0{canonical_key}".encode("utf-8")
            ).hexdigest()[:20]
            if item_id in seen:
                raise ValueError("Повторное назначение публикации одной ветви.")
            seen.add(item_id)
            items.append({
                "item_id": item_id,
                "target_topic": labels[branch_id],
                "document": {
                    "title": title,
                    "abstract": work.get("abstract"),
                    "abstract_included": bool(work.get("abstract")),
                    "first_submission_date": work.get("published_at"),
                    "categories": [],
                    "url": url,
                },
                "questions": copy.deepcopy(questions),
                "evidence_boundary": (
                    "Оценивается только соответствие публикации указанной ветви. "
                    "Это не оценка слабого сигнала, рынка или будущего успеха."
                ),
            })

    # Hide merge rank and source/branch volume.  The stable hash order is
    # deterministic, but unrelated to the original result position.
    items.sort(key=lambda item: hashlib.sha256(
        f"{package_id}\0{item['item_id']}".encode("utf-8")
    ).hexdigest())
    packet = {
        "version": DYNAMIC_PACKET_VERSION,
        "package_id": package_id,
        "purpose": "independent_relevance_review_of_one_balanced_query_result",
        "policy_version": load_policy()["version"],
        "source_job_id": str(job["job_id"]),
        "source_result_sha256": job["result_sha256"],
        "approved_query_plan_id": str(job["approved_query_plan_id"]),
        "selection_summary": {
            "algorithm": "all-returned-work-branch-assignments-v1",
            "assignments": len(items),
            "unique_documents": len(works),
            "all_returned_documents_included": True,
            "original_result_order_hidden": True,
            "micro_precision_supported": False,
        },
        "blinding": {
            "original_result_order_hidden": True,
            "branch_and_source_counts_hidden_per_item": True,
            "expected_labels_present": False,
        },
        "reviewer_instructions": [
            "Оцените публикацию только относительно указанной целевой темы.",
            "Не оценивайте перспективность, рынок или статус слабого сигнала.",
            "Если данных недостаточно, выберите uncertain.",
            "Заполните анкету независимо от другого рецензента.",
        ],
        "items": items,
        "gold_standard": False,
        "calibration_allowed": False,
        "production_change_allowed": False,
    }
    packet["packet_payload_sha256"] = digest(packet)
    template = {
        "version": SUBMISSION_VERSION,
        "package_id": package_id,
        "packet_payload_sha256": packet["packet_payload_sha256"],
        "reviewer_id": None,
        "independent_review_declared": None,
        "hidden_fields_not_seen_declared": None,
        "annotations": [{
            "item_id": item["item_id"],
            "answers": {name: None for name in questions},
            "rationale": None,
            "sources": [],
        } for item in items],
    }
    return packet, template


def from_job_id(job_id: str) -> tuple[dict, dict]:
    from saia.jobs import read
    return build_packet(read(job_id))
