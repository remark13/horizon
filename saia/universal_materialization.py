"""Materialize one universal query into the frozen arXiv pipeline.

Candidate generation is automatic once a compiled collection job succeeds.
Retrieval adjudication is optional evidence about search quality and is never a
gate for producing weak-signal candidates.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta

from psycopg.types.json import Jsonb

from saia import db
from saia.arxiv_metadata import LITERAL_MATCHING_VERSION
from saia.balanced_retrieval_adjudication import read as read_adjudication
from saia.jobs import read as read_job
from saia.query_expansion import digest


VERSION = "universal-candidate-materialization-0.4.48"


def _actor(value: str) -> str:
    value = " ".join((value or "").split())
    if not 1 <= len(value) <= 120:
        raise ValueError("Укажите автора запуска анализа.")
    return value


def _compiled_scope(job: dict) -> tuple[list[dict], list[str], list[str], bool]:
    if job.get("job_kind") != "approved_balanced_discovery" or job.get("status") != "succeeded":
        raise ValueError("Полный анализ требует успешно завершённого многоветочного сбора.")
    specs = (job.get("payload") or {}).get("compiled_branch_specs")
    if not isinstance(specs, list) or not specs:
        raise ValueError(
            "Для полного корпуса нужен сохранённый compiled exact-phrase plan."
        )
    branch_ids = [spec.get("branch_id") for spec in specs]
    approved_ids = [branch.get("branch_id") for branch in (job.get("payload") or {}).get("branches") or []]
    if branch_ids != approved_ids or len(branch_ids) != len(set(branch_ids)):
        raise ValueError("Скомпилированные фразы не совпадают с подтверждёнными ветвями.")
    # The original free query is mandatory in preview/provenance. Once the
    # user confirms narrower branches, its literal text must not be OR-ed
    # back into the full arXiv cohort: a single ambiguous word such as "Edge"
    # otherwise swamps the selected scientific subthemes with tens of
    # thousands of unrelated records. With no selected branches, preserve
    # the original-only behavior.
    optional = [spec for spec in specs if spec["branch_id"] != "original-query"]
    full_specs = optional or specs
    original_preview_only = bool(optional)
    included = list(dict.fromkeys(
        " ".join(phrase.split())
        for spec in full_specs for phrase in spec.get("included_phrases") or []
    ))
    excluded = list(dict.fromkeys(
        " ".join(phrase.split())
        for spec in full_specs for phrase in spec.get("excluded_phrases") or []
    ))
    if not included or any(not phrase for phrase in [*included, *excluded]):
        raise ValueError("Compiled exact-phrase plan содержит пустые фразы.")
    if set(included) & set(excluded):
        raise ValueError("Одна фраза не может одновременно включаться и исключаться.")
    versions = {spec.get("matching_version", LITERAL_MATCHING_VERSION)
                for spec in full_specs}
    if len(versions) != 1:
        raise ValueError("Нельзя смешивать версии сопоставления фраз в одном полном корпусе.")
    return specs, included, excluded, original_preview_only


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def _complete_month_bounds(start: date, as_of: date) -> tuple[date, date]:
    """Return full-month bounds contained inside the requested interval."""
    aligned_start = start if start.day == 1 else _next_month(start)
    aligned_as_of = date(as_of.year, as_of.month, 1)
    if aligned_start >= aligned_as_of:
        raise ValueError(
            "В запрошенном периоде нет ни одного полностью завершённого календарного месяца."
        )
    return aligned_start, aligned_as_of


def _profile(
    job: dict,
    initiated_by: str,
    *,
    adjudication_record: dict | None = None,
    candidate_source_mode: str = "auto",
) -> tuple[dict, dict]:
    initiated_by = _actor(initiated_by)
    if candidate_source_mode not in {"auto", "complete_arxiv"}:
        raise ValueError("Unknown candidate source mode")
    if adjudication_record is not None and candidate_source_mode != "auto":
        raise ValueError("Adjudicated materialization has a fixed source contract")
    specs, included, excluded, original_preview_only = _compiled_scope(job)
    full_specs = [spec for spec in specs if spec["branch_id"] != "original-query"] or specs
    compound = any(spec.get("concept_groups") for spec in full_specs)
    matching_version = full_specs[0].get("matching_version", LITERAL_MATCHING_VERSION)
    requested_as_of = date.fromisoformat(job["payload"]["as_of_date"])
    requested_start = date.fromisoformat(job["payload"]["date_from"])
    if requested_start >= requested_as_of:
        raise ValueError("Период задачи пуст.")
    start, as_of = _complete_month_bounds(requested_start, requested_as_of)
    # A successful balanced discovery is itself a frozen, bounded scientific
    # cohort.  Automatic candidate generation must not discard its OpenAlex
    # publications.  Historical/adjudicated arXiv-only runs retain their
    # original source contract.
    preview_works = (job.get("result") or {}).get("works") or []
    bounded_sources = sorted({
        source for work in preview_works
        if isinstance(work, dict) and work.get("published_at")
        and (not original_preview_only or
             set(work.get("branch_provenance") or []) != {"original-query"})
        and start <= date.fromisoformat(work["published_at"][:10]) < as_of
        for source in (work.get("source_provenance") or work.get("sources") or [])
        if source in {"openalex", "arxiv"}
    })
    bounded_live_arxiv = (job.get("result") or {}).get(
        "source_modes", {}).get("arxiv") == "live_api_bounded_compiled_metadata"
    if (bounded_live_arxiv and adjudication_record is None
            and candidate_source_mode == "auto" and not bounded_sources):
        raise ValueError(
            "В ограниченной выдаче не осталось публикаций для выбранных подтем "
            "и полных календарных месяцев. Уточните запрос или выбор подтем. "
            "Исходный запрос, оставленный только для предпросмотра, "
            "не расширяет область анализа."
        )
    bounded_mode = (adjudication_record is None
                    and candidate_source_mode == "auto") and (
        "openalex" in bounded_sources or compound
        or bounded_live_arxiv)
    sources = bounded_sources if bounded_mode else ["arxiv"]

    provenance = {
        "approved_query_plan_id": str(job["approved_query_plan_id"]),
        "balanced_discovery_job_id": str(job["job_id"]),
        "balanced_result_sha256": job["result_sha256"],
        "compiled_query_plan_id": job["payload"].get("compiled_query_plan_id"),
        "compiled_plan_payload_sha256": job["payload"].get("compiled_plan_payload_sha256"),
    }
    if candidate_source_mode == "complete_arxiv":
        provenance["candidate_source_mode"] = "complete_arxiv"
    relation = (
        "explicit_union_of_selected_branch_phrases_original_query_preview_only"
        if original_preview_only else "original_query_exact_phrases_only"
    )
    if compound:
        relation = (
            "explicit_union_of_selected_branch_predicates_original_query_preview_only"
            if original_preview_only else "original_query_literal_or_required_concepts"
        )
    decision = (
        "automatic_candidate_generation_from_frozen_bounded_discovery"
        if bounded_mode else
        "automatic_candidate_generation_from_compiled_local_arxiv_scope"
    )
    identifier = (f"monthly-full-arxiv-v1-{job['job_id']}"
                  if candidate_source_mode == "complete_arxiv"
                  else f"monthly-v2-{job['job_id']}")
    retrieval_validation_status = "not_requested"
    if adjudication_record is not None:
        adjudication = adjudication_record.get("adjudication") or {}
        if (
            adjudication.get("source_job_id") != str(job.get("job_id"))
            or adjudication.get("complete") is not True
            or adjudication.get("precision_available") is not True
            or adjudication.get("weak_signal_accuracy_measured") is not False
        ):
            raise ValueError("Указан неполный арбитраж retrieval-пакета этой задачи.")
        identifier = f"adjudicated-v2-{adjudication_record['adjudication_id']}"
        relation = (
            "explicit_union_of_adjudicated_selected_branches_original_preview_only"
            if original_preview_only else "adjudicated_original_query_exact_phrases_only"
        )
        decision = "candidate_generation_with_optional_retrieval_adjudication"
        retrieval_validation_status = "completed"
        provenance.update({
            "retrieval_adjudication_id": str(adjudication_record["adjudication_id"]),
            "retrieval_adjudication_payload_sha256": adjudication[
                "adjudication_payload_sha256"
            ],
            "retrieval_precision_scope": adjudication["precision_scope"],
            "retrieval_metrics": copy.deepcopy(adjudication["metrics"]),
        })

    mission_id = f"universal-{identifier}"
    query_version_id = f"{mission_id}/v1"
    original_query = next(
        branch["query"] for branch in job["payload"]["branches"]
        if branch["branch_id"] == "original-query"
    )
    plan = {
        "version": VERSION + ("+literal-or-concept-groups-v1" if compound
                              else "+exact-phrase-union-v1"),
        "original_query": original_query,
        "included_terms": included,
        "exclusions": excluded,
        "matching_version": matching_version,
        "date_from": start.isoformat(),
        "as_of_date": as_of.isoformat(),
        "relation": relation,
        "original_query_used_for_preview_only": original_preview_only,
        "automatic_translation": False,
        "interpretation": (
            ("Подтверждённый составной план сохраняет OR между исходной фразой и "
             "совместным наличием обязательных понятий; синонимы внутри понятия "
             "объединены по OR. Это граница извлечения, не подтверждение сигнала."
             if compound else
             "Подтверждённые тематические фразы объединены по OR для построения "
             "воспроизводимого корпуса; исходный запрос остаётся в предпросмотре "
             "и происхождении, но не расширяет полный корпус, когда выбраны подтемы. "
             "Это техническая граница поиска, а не "
             "экспертное подтверждение слабого сигнала.")
        ),
    }
    if compound:
        plan["compiled_branch_specs"] = copy.deepcopy(full_specs)
    profile = {
        "mission_id": mission_id,
        "query_version": query_version_id,
        "title": f"Универсальный запрос: {original_query}",
        "question": (
            "Какие воспроизводимые ранние научные линии присутствуют внутри "
            f"запроса «{original_query}»?"
        ),
        "as_of_date": as_of.isoformat(),
        "period": {"from": start.isoformat(), "to": (as_of - timedelta(days=1)).isoformat()},
        "requested_bounds": {
            "date_from": requested_start.isoformat(),
            "as_of_date": requested_as_of.isoformat(),
            "policy": "complete_calendar_months_only",
            "partial_boundary_days_excluded": (
                requested_start != start or requested_as_of != as_of
            ),
        },
        "sources": sources,
        "query": {"terms": included, "exclusions": excluded, "arxiv_categories": []},
        "parent_field": ({
            "label": "Bounded balanced OpenAlex/arXiv discovery sample",
            "scope": "nonrepresentative_bounded_sample",
            "limitation": "Временные доли и рост по этой выборке несопоставимы с полным полем.",
        } if bounded_mode else {
            "label": "Entire pinned arXiv snapshot in the same period",
            "scope": "all_categories",
            "limitation": "Это знаменатель arXiv, а не мировой науки или рынка.",
        }),
        "controlled_search_plan": plan,
        "expansion_source": "controlled_source_profile",
        "collection_profile": {
            "version": VERSION,
            "base_query_version_id": query_version_id,
            "selected_sources": sources,
            "reviewed_by": initiated_by,
            "decision": (
                ("bounded_compound_concept_candidates" if compound else
                 "bounded_balanced_openalex_arxiv_candidates")
                if bounded_mode else decision
            ),
            "openalex_observation": {
                "status": "frozen_bounded_candidate_input" if "openalex" in sources and bounded_mode
                          else "not_collected_by_full_job",
                "query_role": "candidate_input_nonrepresentative" if "openalex" in sources and bounded_mode
                              else "bounded_retrieval_review_only",
                "source_reported_count": None,
                "observed_at": None,
            },
            "provenance": provenance,
            "branch_specs": copy.deepcopy(specs),
            "original_query_used_for_preview_only": original_preview_only,
            "limitations": ([
                "Научные источники из завершённой ограниченной поисковой выдачи участвуют в построении тем.",
                "Это выборка с лимитами и балансировкой, не полный корпус; рост доли и историческая новизна не подтверждаются.",
                "Недоступные цитирования и организации не заполняются предположениями.",
                *(["Составной план проверяет совместность понятий в названии и аннотации; это не доказательство содержательной релевантности."]
                  if compound else []),
            ] if bounded_mode else [
                "Полный корпус ограничен закреплённым зеркалом arXiv.",
                "При выбранных подтемах свободный исходный запрос остаётся в предпросмотре, но не расширяет полный корпус.",
                "Фразы выбранных ветвей объединены по OR; branch-level границы не переносятся в score.",
                "OpenAlex остаётся проверенной ограниченной выдачей, а не полным корпусом.",
                "Проверка retrieval, если она выполнена, измеряет релевантность поиска, а не качество слабых сигналов.",
            ]),
        },
        "expert_validation": {
            "required_for_candidate_generation": False,
            "signal_validation_status": "not_requested",
            "retrieval_validation_status": retrieval_validation_status,
            "interpretation": (
                "Система формирует кандидатов автоматически. Пользователь может "
                "позже добровольно направить выбранные карточки экспертам."
            ),
        },
        "materialization": {
            "version": VERSION,
            "initiated_by": initiated_by,
            "automatic_candidate_generation": True,
            "automatic_execution": False,
            "analysis_uses_complete_calendar_months": True,
            "weak_signal_decision": False,
        },
    }
    if candidate_source_mode == "complete_arxiv":
        profile["materialization"]["candidate_source_mode"] = candidate_source_mode
    mission = {
        "mission_id": mission_id, "title": profile["title"],
        "question": profile["question"], "as_of_date": as_of,
        "period_from": start, "period_to": as_of - timedelta(days=1),
        "languages": [], "regions": [], "sources": sources,
        "created_by": initiated_by,
    }
    return mission, profile


def build_profile(
    job: dict,
    adjudication_record: dict,
    accepted_by: str,
    *,
    acknowledge_arxiv_only: bool,
    acknowledge_phrase_union: bool,
) -> tuple[dict, dict]:
    """Backward-compatible materialization with optional retrieval evidence."""
    if acknowledge_arxiv_only is not True:
        raise ValueError("Подтвердите, что полный корпус этого запуска ограничен arXiv.")
    if acknowledge_phrase_union is not True:
        raise ValueError(
            "Подтвердите объединение точных фраз всех ветвей по правилу OR."
        )
    return _profile(job, accepted_by, adjudication_record=adjudication_record)


def build_candidate_profile(
    job: dict, requested_by: str, *, source_mode: str = "auto",
) -> tuple[dict, dict]:
    """Build an automatic candidate run without any expert-validation gate."""
    return _profile(job, requested_by, candidate_source_mode=source_mode)


def materialize(
    job_id: str,
    adjudication_id: str,
    accepted_by: str,
    *,
    acknowledge_arxiv_only: bool,
    acknowledge_phrase_union: bool,
) -> dict:
    job = read_job(job_id)
    adjudication = read_adjudication(job_id, adjudication_id)
    mission, profile = build_profile(
        job, adjudication, accepted_by,
        acknowledge_arxiv_only=acknowledge_arxiv_only,
        acknowledge_phrase_union=acknowledge_phrase_union,
    )
    return _persist(job, mission, profile, adjudication_id=str(adjudication_id))


def materialize_candidate(
    job_id: str, requested_by: str, *, source_mode: str = "auto",
) -> dict:
    """Materialize automatic candidates; expert review may happen afterwards."""
    job = read_job(job_id)
    mission, profile = build_candidate_profile(job, requested_by, source_mode=source_mode)
    return _persist(job, mission, profile, adjudication_id=None)


def _persist(job: dict, mission: dict, profile: dict, *, adjudication_id: str | None) -> dict:
    query_id = profile["query_version"]
    profile_hash = digest(profile)
    expected_mission = (
        mission["title"], mission["question"], mission["as_of_date"],
        mission["period_from"], mission["period_to"], mission["languages"],
        mission["regions"], mission["sources"], mission["created_by"],
    )
    expected_query = (
        mission["mission_id"], 1, profile["query"]["terms"],
        profile["query"]["exclusions"], profile["parent_field"],
        "controlled_source_profile", profile, profile_hash,
    )
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mission (mission_id,title,question,as_of_date,period_from,"
            "period_to,languages,regions,sources,created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (mission["mission_id"], *expected_mission),
        )
        mission_inserted = cur.rowcount == 1
        cur.execute(
            "SELECT title,question,as_of_date,period_from,period_to,languages,regions,"
            "sources,created_by FROM mission WHERE mission_id=%s FOR UPDATE",
            (mission["mission_id"],),
        )
        if tuple(cur.fetchone()) != expected_mission:
            raise ValueError("Детерминированная миссия уже существует с другим содержимым.")
        cur.execute(
            "INSERT INTO query_version (query_version_id,mission_id,version,terms,exclusions,"
            "parent_field,expansion_source,payload,content_sha256) "
            "VALUES (%s,%s,1,%s,%s,%s,'controlled_source_profile',%s,%s) "
            "ON CONFLICT DO NOTHING",
            (query_id, mission["mission_id"], profile["query"]["terms"],
             profile["query"]["exclusions"], Jsonb(profile["parent_field"]),
             Jsonb(profile), profile_hash),
        )
        query_inserted = cur.rowcount == 1
        cur.execute(
            "SELECT mission_id,version,terms,exclusions,parent_field,expansion_source,"
            "payload,content_sha256 FROM query_version WHERE query_version_id=%s",
            (query_id,),
        )
        if tuple(cur.fetchone()) != expected_query:
            raise ValueError("Детерминированный профиль уже существует с другим содержимым.")
    return {
        "version": VERSION,
        "source_job_id": str(job["job_id"]), "adjudication_id": adjudication_id,
        "mission_id": mission["mission_id"], "query_version_id": query_id,
        "query_profile_sha256": profile_hash,
        "source_scope": profile["sources"],
        "phrase_union": {"included": profile["query"]["terms"],
                         "excluded": profile["query"]["exclusions"]},
        "replayed": not mission_inserted and not query_inserted,
        "execution_started": False,
        "expert_validation_required": False,
        "expert_validation_status": "not_requested",
        "weak_signal_decision": False,
    }
