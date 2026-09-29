"""Collect approved branches independently and merge them without scoring."""
from __future__ import annotations

from collections import deque
from datetime import date
import os
from pathlib import Path

from saia.balanced_merge import merge
from saia.compiled_phrase_matching import matches_spec
from saia.discovery import discover
from saia.openalex_boolean_query import concept_expression


LEGACY_VERSION = "approved-balanced-discovery-0.4.36"
VERSION = "approved-balanced-discovery-0.4.56"
RESULT_ROLE = "balanced_corpus_candidate_not_signals"


def _mix_live_prepared(live: list[dict], prepared: list[dict]) -> list[dict]:
    """Keep live results first while making a bounded supplement visible."""
    live_queue, prepared_queue = deque(live), deque(prepared)
    mixed = []
    while live_queue or prepared_queue:
        for _ in range(2):
            if live_queue:
                mixed.append(live_queue.popleft())
        if prepared_queue:
            mixed.append(prepared_queue.popleft())
    return mixed


def _source_rows(works: list[dict]) -> dict[str, list[dict]]:
    rows = {"openalex": [], "arxiv": []}
    for work in works:
        for source in rows:
            if source in (work.get("sources") or []):
                rows[source].append(dict(work))
    return rows


def run(payload: dict, collector=discover, local_scanner=None,
        openalex_cache_scanner=None, prepared_openalex_scanner=None) -> dict:
    if payload.get("contract_version") not in {LEGACY_VERSION, VERSION}:
        raise ValueError("Неизвестная версия задачи многоветочного сбора.")
    start = date.fromisoformat(payload["date_from"])
    cutoff = date.fromisoformat(payload["as_of_date"])
    if start >= cutoff:
        raise ValueError("Начало периода должно быть раньше даты среза.")
    branches = payload.get("branches") or []
    if not 1 <= len(branches) <= 12:
        raise ValueError("Один запуск должен содержать от 1 до 12 подтверждённых ветвей.")
    compiled_specs = payload.get("compiled_branch_specs")
    compiled_by_id = {spec["branch_id"]: spec for spec in compiled_specs or []}
    openalex_mode = payload.get("openalex_collection_mode", "live")
    if openalex_mode not in ("live", "cache_year_spread", "live_with_cache_fallback",
                            "compound_boolean_live", "live_with_prepared_supplement"):
        raise ValueError("Неизвестный режим сбора OpenAlex.")
    if openalex_mode in {"cache_year_spread", "live_with_cache_fallback",
                         "compound_boolean_live", "live_with_prepared_supplement"} and compiled_specs is None:
        raise ValueError("Для выбранного режима OpenAlex нужен скомпилированный план.")
    local_report = None
    local_by_branch = {}
    openalex_cache_report = None
    openalex_cache_scan_error = None
    cached_by_branch = {}
    prepared_report = None
    prepared_by_branch = {}
    prepared_scan_error = None
    bounded_live_arxiv = False
    if compiled_specs is not None:
        directory = os.environ.get("SAIA_ARXIV_MIRROR_DIR")
        if not directory:
            if os.environ.get("SAIA_ALLOW_LIVE_ARXIV") != "1":
                raise ValueError("Для скомпилированного плана не подключён локальный arXiv.")
            bounded_live_arxiv = True
        elif local_scanner is None:
            from saia.local_arxiv_multibranch import scan
            local_report = scan(
                directory, compiled_specs, start, cutoff, payload["limit_per_source"],
                selection_strategy="year_balanced_hash_v1")
        else:
            local_report = local_scanner(
                directory, compiled_specs, start, cutoff, payload["limit_per_source"])
        local_by_branch = {
            item["branch_id"]: item for item in (local_report or {}).get("branches", [])
        }
        if openalex_mode in {"cache_year_spread", "live_with_cache_fallback"}:
            if openalex_cache_scanner is None:
                from saia.openalex_cache_search import scan as openalex_cache_scanner
            try:
                openalex_cache_report = openalex_cache_scanner(
                    compiled_specs, start, cutoff, payload["limit_per_source"])
                cached_by_branch = {
                    item["branch_id"]: item for item in openalex_cache_report["branches"]
                }
            except Exception as error:
                if openalex_mode == "cache_year_spread":
                    raise
                openalex_cache_scan_error = f"{type(error).__name__}: {error}"[:1000]
        if openalex_mode == "live_with_prepared_supplement":
            if prepared_openalex_scanner is None:
                from saia.prepared_openalex_search import scan as prepared_openalex_scanner
                prepared_dir = os.environ.get("SAIA_PREPARED_OPENALEX_DIR")
                if not prepared_dir:
                    prepared_scan_error = "SAIA_PREPARED_OPENALEX_DIR is not configured"
            else:
                prepared_dir = "."
            if prepared_scan_error is None:
                try:
                    prepared_report = prepared_openalex_scanner(
                        Path(prepared_dir), compiled_specs, start, cutoff,
                        payload["limit_per_source"])
                    prepared_by_branch = {
                        item["branch_id"]: item for item in prepared_report["branches"]
                    }
                except Exception as error:
                    prepared_scan_error = f"{type(error).__name__}: {error}"[:1000]
    branch_collector = collector
    if local_report is not None and collector is discover:
        from saia.discovery import discover_openalex_only
        branch_collector = discover_openalex_only
    collected_branches = []
    branch_audit = []
    all_errors = {}
    if openalex_cache_scan_error:
        all_errors["openalex_cache_scan"] = openalex_cache_scan_error
    if prepared_scan_error:
        all_errors["prepared_openalex_scan"] = prepared_scan_error
    for branch in branches:
        branch_id, query = branch.get("branch_id"), branch.get("query")
        if not branch_id or not isinstance(query, str) or not query.strip():
            raise ValueError("В подтверждённом плане обнаружена пустая ветвь.")
        try:
            cached = cached_by_branch.get(branch_id) if openalex_cache_report else None
            if openalex_mode == "cache_year_spread" and cached is None:
                raise ValueError(f"Кэш OpenAlex не вернул ветвь {branch_id}.")
            spec = compiled_by_id.get(branch_id)
            api_query = (concept_expression(spec["concept_groups"])
                         if openalex_mode == "compound_boolean_live"
                         and spec and spec.get("concept_groups") else query)
            if openalex_mode == "cache_year_spread" and cached and cached["works"]:
                result = {"works": cached["works"], "errors": {}}
                openalex_origin = "previously_ingested_local_cache"
            else:
                try:
                    value = branch_collector(api_query, start, cutoff,
                                             payload["limit_per_source"])
                    result = value.to_dict() if hasattr(value, "to_dict") else value
                except Exception as error:
                    if openalex_mode not in {"live_with_cache_fallback",
                                            "live_with_prepared_supplement"}:
                        raise
                    result = {"works": [], "errors": {
                        "openalex": f"{type(error).__name__}: {error}"[:1000]}}
                if not isinstance(result, dict):
                    raise ValueError("Коллектор вернул результат неизвестного формата.")
                openalex_origin = ("live_api_boolean_concept_preview"
                                   if openalex_mode == "compound_boolean_live"
                                   and spec and spec.get("concept_groups") else
                                   "live_api_current_metadata")
                if (openalex_mode == "live_with_cache_fallback"
                        and (result.get("errors") or {}).get("openalex")
                        and not result.get("works") and cached and cached.get("works")):
                    result = {"works": cached["works"],
                              "errors": dict(result["errors"])}
                    openalex_origin = "incomplete_local_cache_after_live_api_failure"
            source_rows = _source_rows(list(result.get("works") or []))
            openalex_before_compiled_filter = len(source_rows["openalex"])
            apply_compiled_filter = bool(
                spec and openalex_origin.startswith("live_api")
                and (payload["contract_version"] == VERSION
                     or spec.get("concept_groups") or bounded_live_arxiv))
            if apply_compiled_filter:
                # OpenAlex search returns bounded candidates with its own loose
                # relevance semantics. Apply the same approved exact predicate
                # used by the pinned arXiv mirror before merging. Cache scanner
                # and local arXiv already enforce this predicate themselves.
                source_rows["openalex"] = [
                    work for work in source_rows["openalex"] if matches_spec(work, spec)
                ]
            live_after_compiled_filter = len(source_rows["openalex"])
            arxiv_before_compiled_filter = len(source_rows["arxiv"])
            if bounded_live_arxiv and spec:
                # Portable startup has no full mirror. Keep the same approved
                # predicate on the bounded live arXiv hits, including exclusions.
                source_rows["arxiv"] = [
                    work for work in source_rows["arxiv"] if matches_spec(work, spec)
                ]
            prepared_added = 0
            prepared_duplicate = 0
            if openalex_mode == "live_with_prepared_supplement":
                prepared_branch = prepared_by_branch.get(branch_id)
                live_rows = [
                    {**work, "retrieval_origins": ["live_openalex_api"]}
                    for work in source_rows["openalex"]
                ]
                live_keys = {
                    work.get("canonical_key") for work in live_rows
                }
                live_ids = {
                    source_id for work in live_rows
                    for source_id in work.get("source_ids") or []
                }
                added_rows = []
                for prepared_work in (prepared_branch or {}).get("works") or []:
                    key = prepared_work.get("canonical_key")
                    ids = set(prepared_work.get("source_ids") or [])
                    if key in live_keys or ids & live_ids:
                        prepared_duplicate += 1
                        continue
                    added_rows.append({**prepared_work, "retrieval_origins": [
                        "pinned_query_specific_openalex_cohort"]})
                    live_keys.add(key)
                    live_ids.update(ids)
                    prepared_added += 1
                source_rows["openalex"] = _mix_live_prepared(live_rows, added_rows)
                if prepared_added:
                    openalex_origin = (
                        "pinned_query_cohort_after_live_api_failure"
                        if (result.get("errors") or {}).get("openalex") else
                        "live_api_plus_pinned_query_cohort_supplement"
                    )
            errors = dict(result.get("errors") or {})
            if local_report is not None:
                local_branch = local_by_branch.get(branch_id)
                if local_branch is None:
                    raise ValueError(f"Локальный arXiv не вернул ветвь {branch_id}.")
                source_rows["arxiv"] = list(local_branch["works"])
                errors.pop("arxiv", None)
            for source, message in errors.items():
                all_errors[f"{branch_id}/{source}"] = message
            collected_branches.append({"branch_id": branch_id, "sources": source_rows})
            branch_audit.append({
                "branch_id": branch_id, "query": query,
                "openalex_api_query": api_query,
                "source_counts": {source: len(rows) for source, rows in source_rows.items()},
                "unique_works_before_merge": len(result.get("works") or []),
                "errors": errors,
                "local_arxiv": (local_by_branch.get(branch_id) if local_report else None),
                **({"arxiv_compiled_phrase_filter": {
                    "bounded_input": arxiv_before_compiled_filter,
                    "accepted": len(source_rows["arxiv"]),
                    "live_recall_proven": False,
                }} if bounded_live_arxiv else {}),
                "openalex_cache": (cached_by_branch.get(branch_id)
                                    if openalex_cache_report else None),
                "prepared_openalex": ({
                    "matched_in_saved_cohorts": prepared_branch.get(
                        "eligible_matches_in_prepared_query_cohorts"),
                    "selected": len(prepared_branch.get("works") or []),
                    "added_after_live_dedup": prepared_added,
                    "duplicate_of_live_or_prepared": prepared_duplicate,
                    "manifest_sha256": prepared_report.get("manifest_sha256"),
                    "complete_for_arbitrary_query": False,
                } if openalex_mode == "live_with_prepared_supplement"
                     and prepared_branch is not None else None),
                "openalex_origin": openalex_origin,
                "openalex_compiled_phrase_filter": (
                    {"bounded_input": openalex_before_compiled_filter,
                     "accepted": live_after_compiled_filter,
                     "live_recall_proven": False}
                    if apply_compiled_filter else None),
                "openalex_compound_filter": (
                    {"bounded_input": openalex_before_compiled_filter,
                     "accepted": live_after_compiled_filter,
                     "live_recall_proven": False}
                    if apply_compiled_filter and spec.get("concept_groups") else None),
            })
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:1000]
            all_errors[f"{branch_id}/collector"] = message
            local_branch = local_by_branch.get(branch_id)
            surviving_arxiv = list(local_branch.get("works") or []) if local_branch else []
            collected_branches.append({
                "branch_id": branch_id,
                "sources": {"openalex": [], "arxiv": surviving_arxiv},
            })
            branch_audit.append({
                "branch_id": branch_id, "query": query,
                "source_counts": {"openalex": 0, "arxiv": len(surviving_arxiv)},
                "unique_works_before_merge": 0, "errors": {"collector": message},
                "local_arxiv": local_branch,
            })
    merged = merge(collected_branches, payload["max_results"])
    if openalex_mode == "live_with_prepared_supplement":
        merged["retrieval_origin_counts_in_selected_results"] = {
            origin: sum(origin in (work.get("retrieval_origins") or [])
                        for work in merged["results"])
            for origin in ("live_openalex_api",
                           "pinned_query_specific_openalex_cohort")
        }
    return {
        "version": payload["contract_version"],
        "result_role": RESULT_ROLE,
        "approved_query_plan_id": payload["approved_query_plan_id"],
        "approved_plan_payload_sha256": payload["approved_plan_payload_sha256"],
        "date_from": payload["date_from"],
        "as_of_date": payload["as_of_date"],
        "limit_per_source": payload["limit_per_source"],
        "branches": branch_audit,
        "merge": merged,
        "works": merged["results"],
        "errors": all_errors,
        "source_modes": {
            "openalex": ("previously_ingested_local_cache_with_live_fallback"
                         if openalex_mode == "cache_year_spread" else
                         "live_api_with_incomplete_cache_on_explicit_failure"
                         if openalex_mode == "live_with_cache_fallback" else
                         "live_api_plus_pinned_query_cohort_supplement"
                         if openalex_mode == "live_with_prepared_supplement" else
                         "live_api_boolean_concept_preview"
                         if openalex_mode == "compound_boolean_live" else
                         "live_api_current_metadata"),
            "arxiv": ("pinned_local_metadata_snapshot" if local_report
                      else "live_api_bounded_compiled_metadata" if bounded_live_arxiv
                      else "live_api_current_metadata"),
            "local_arxiv": ("explicit_exact_phrase_one_pass" if local_report
                            else "not_configured_explicit_live_api_mode" if bounded_live_arxiv
                            else "not_used_until_branch_terms_are_explicitly_compiled"),
        },
        "compiled_query_plan_id": payload.get("compiled_query_plan_id"),
        "compiled_plan_payload_sha256": payload.get("compiled_plan_payload_sha256"),
        "local_arxiv_audit": local_report,
        "openalex_cache_audit": openalex_cache_report,
        "openalex_cache_scan_error": openalex_cache_scan_error,
        "prepared_openalex_audit": prepared_report,
        "prepared_openalex_scan_error": prepared_scan_error,
        "coverage_comparable": None,
        "scientific_score_calculated": False,
        "weak_signal_assessment_performed": False,
        "limitations": [
            "Это ограниченный многоветочный корпус-кандидат, а не полный корпус области.",
            "Текущие метаданные OpenAlex не воспроизводят историческое состояние базы.",
            ("OpenAlex получен из прежних загрузок Horizon; это неполный кэш, а не свежая выдача API."
             if openalex_mode == "cache_year_spread" else
             "OpenAlex запрашивается через живой API; неполный локальный кэш используется только при явном сбое API и отмечается в каждой затронутой ветви."
             if openalex_mode == "live_with_cache_fallback" else
             "Живой OpenAlex дополнен только совпадениями в сохранённых запросных выборках; они не являются полным индексом произвольной темы, а их годовые счётчики не доказывают рост."
             if openalex_mode == "live_with_prepared_supplement" else
             "OpenAlex получен через ограниченную живую выдачу API; логический запрос не доказывает её полноту."
             if openalex_mode == "compound_boolean_live" else
             "OpenAlex получен через ограниченную живую выдачу API."),
            ("Локальный arXiv выполнен одним проходом по явно подтверждённым точным фразам."
             if local_report else
             "Локальное зеркало arXiv не подключено: явно включена ограниченная живая выдача API с проверкой подтверждённых фраз. Полнота и исторический охват не установлены."
             if bounded_live_arxiv else
             "Локальный arXiv не запускается по автоматически разобранным словам: для него нужен отдельно подтверждённый набор точных фраз."),
            "Round-robin обеспечивает представленность ветвей, но не измеряет качество или силу сигнала.",
            *(["При составном плане живая ограниченная выдача OpenAlex проходит локальную проверку понятий; полнота её поиска не доказана."]
              if any(spec.get("concept_groups") for spec in compiled_specs or []) else []),
        ],
    }
