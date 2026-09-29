"""Durable bounded collection-to-cards pipeline for one approved query.

This module deliberately builds a scientific review queue.  It does not turn
publication dynamics into a market forecast and it does not treat unknown
coverage or independence checks as passed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from psycopg.types.json import Jsonb

from saia import db, methodology, runs
from saia.query_expansion import digest


PROFILE_VERSION = "controlled-source-profile-0.4.17"
CONTRACT_VERSION = "controlled-full-analysis-job-v1"
RESULT_ROLE = "scientific_review_queue_not_market_forecast"
DEFAULT_MAX_RECORDS = 10_000
MAX_RECORDS = 20_000


class FullAnalysisCancelled(RuntimeError):
    pass


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _validate_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-/]*", value):
        raise ValueError(f"Некорректный {label}.")
    return value


def expected_model_name() -> str:
    provider = (os.environ.get("SAIA_EMBEDDING_PROVIDER") or "ollama").lower()
    if provider in {"specter2", "specter2_proximity"}:
        from saia.embed import SPECTER2_ADAPTER_REVISION, SPECTER2_BASE_REVISION
        return (
            f"specter2/proximity@{SPECTER2_BASE_REVISION[:8]}+"
            f"{SPECTER2_ADAPTER_REVISION[:8]}"
        )
    if provider in {"hashing", "hashing_ngram"}:
        return f"hashing-ngram/v2-d{int(os.environ.get('SAIA_HASHING_DIM', '384'))}"
    if provider == "ollama":
        from saia.embed import DEFAULT_OLLAMA_MODEL
        return f"ollama/{os.environ.get('SAIA_EMBEDDING_MODEL') or DEFAULT_OLLAMA_MODEL}"
    if provider in {"sentence_transformers", "st"}:
        return f"st/{os.environ.get('SAIA_EMBEDDING_MODEL') or 'allenai-specter'}"
    raise ValueError(f"Неизвестный провайдер эмбеддингов: {provider}")


def ensure_arxiv_source_profile(cur, mission_id: str, base_query_version_id: str,
                                reviewed_by: str) -> tuple[str, dict, bool]:
    """Reuse or create an immutable arXiv execution profile.

    The source profile preserves the approved semantic plan.  It only records
    that the complete scientific corpus for this bounded job is local arXiv;
    OpenAlex remains a separate preview/enrichment channel.
    """
    _validate_identifier(mission_id, "mission_id")
    _validate_identifier(base_query_version_id, "query_version_id")
    cur.execute("SELECT 1 FROM mission WHERE mission_id=%s FOR UPDATE", (mission_id,))
    if not cur.fetchone():
        raise ValueError("Миссия не найдена.")
    cur.execute(
        "SELECT version,expansion_source,payload,content_sha256 FROM query_version "
        "WHERE query_version_id=%s AND mission_id=%s",
        (base_query_version_id, mission_id),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("Версия запроса не принадлежит указанной миссии.")
    _version, expansion_source, base, base_hash = row
    if digest(base) != base_hash:
        raise ValueError("Хеш сохранённой версии запроса не совпал.")
    plan = base.get("controlled_search_plan")
    if not isinstance(plan, dict):
        raise ValueError("Нужна подтверждённая управляемая версия запроса.")
    profile = base.get("collection_profile") or {}
    if (expansion_source == "controlled_source_profile"
            and base.get("sources") == ["arxiv"]
            and profile.get("selected_sources") == ["arxiv"]):
        return base_query_version_id, base, True
    if (expansion_source == "controlled_source_profile"
            and base.get("sources") in (["openalex"], ["arxiv", "openalex"])
            and profile.get("selected_sources") == base.get("sources")
            and profile.get("decision") in {
                "bounded_balanced_openalex_arxiv_candidates",
                "bounded_compound_concept_candidates"}
            and (profile.get("provenance") or {}).get("balanced_result_sha256")):
        return base_query_version_id, base, True

    cur.execute(
        "SELECT 1 FROM query_expansion_decision WHERE query_version_id=%s",
        (base_query_version_id,),
    )
    if not cur.fetchone():
        raise ValueError(
            "Полный анализ запускается только для явно подтверждённой версии запроса."
        )

    cur.execute(
        "SELECT query_version_id,payload FROM query_version "
        "WHERE mission_id=%s AND expansion_source='controlled_source_profile' "
        "AND payload->'collection_profile'->>'base_query_version_id'=%s "
        "ORDER BY version DESC",
        (mission_id, base_query_version_id),
    )
    for query_id, payload in cur.fetchall():
        if (payload.get("controlled_search_plan") == plan
                and payload.get("sources") == ["arxiv"]):
            return query_id, payload, True

    cur.execute("SELECT COALESCE(max(version),0)+1 FROM query_version WHERE mission_id=%s",
                (mission_id,))
    next_version = cur.fetchone()[0]
    query_id = f"{mission_id}/v{next_version}"
    payload = json.loads(json.dumps(base))
    payload["query_version"] = query_id
    payload["sources"] = ["arxiv"]
    payload["expansion_source"] = "controlled_source_profile"
    payload["collection_profile"] = {
        "version": PROFILE_VERSION,
        "base_query_version_id": base_query_version_id,
        "selected_sources": ["arxiv"],
        "reviewed_by": reviewed_by,
        "decision": "full_local_arxiv_first_openalex_separate",
        "openalex_observation": {
            "status": "not_collected_by_full_job",
            "query_role": "bounded_preview_or_later_enrichment_only",
            "source_reported_count": None,
            "observed_at": None,
            "reason_omitted": (
                "Ограниченный фоновый запуск не может объявить оперативный предпросмотр "
                "OpenAlex полным покрытием: доступ, лимиты API и ожидаемый объём требуют "
                "отдельного решения."
            ),
        },
        "limitations": [
            "Полный сбор в этом профиле ограничен локальным зеркалом arXiv.",
            "Предпросмотр и обогащение OpenAlex показываются отдельно и не считаются полным покрытием.",
            "Зеркало arXiv содержит текущие метаданные и даты первых версий, но не исторические версии текстов.",
            "Полнота подтверждается только относительно закреплённого зеркала и точного утверждённого условия отбора.",
        ],
    }
    content_hash = digest(payload)
    cur.execute(
        "INSERT INTO query_version (query_version_id,mission_id,version,terms,exclusions,"
        "parent_field,expansion_source,payload,content_sha256) "
        "VALUES (%s,%s,%s,%s,%s,%s,'controlled_source_profile',%s,%s)",
        (query_id, mission_id, next_version, list(payload.get("query", {}).get("terms") or []),
         list(payload.get("query", {}).get("exclusions") or []),
         Jsonb(payload.get("parent_field")), Jsonb(payload), content_hash),
    )
    return query_id, payload, False


def job_payload(mission_id: str, base_query_version_id: str,
                execution_query_version_id: str, execution_profile: dict,
                max_records: int, top_n: int) -> dict:
    if not 100 <= max_records <= MAX_RECORDS:
        raise ValueError(f"Лимит корпуса должен быть от 100 до {MAX_RECORDS} записей.")
    if not 1 <= top_n <= 100:
        raise ValueError("Число карточек должно быть от 1 до 100.")
    sources = execution_profile.get("sources", ["arxiv"])
    request = {
        "contract_version": CONTRACT_VERSION,
        "mission_id": mission_id,
        "base_query_version_id": base_query_version_id,
        "execution_query_version_id": execution_query_version_id,
        "execution_profile_sha256": digest(execution_profile),
        "search_plan_sha256": digest(execution_profile["controlled_search_plan"]),
        "source_scope": sources,
        "openalex_role": (
            "bounded_candidate_input_not_complete_corpus"
            if "openalex" in sources else
            "bounded_preview_or_later_enrichment_not_complete_corpus"
        ),
        "max_records": max_records,
        "top_n": top_n,
        "pipeline": [
            "collect", "ingest", "parent_coverage", "normalize", "quality",
            "embed", "cluster", "historical_novelty", "score", "triage",
        ],
    }
    request["request_sha256"] = digest(request)
    return request


def _load_execution_profile(query_version_id: str, expected_hash: str) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload,content_sha256 FROM query_version WHERE query_version_id=%s",
            (query_version_id,),
        )
        row = cur.fetchone()
    if not row or digest(row[0]) != row[1] or row[1] != expected_hash:
        raise ValueError("Execution source profile is missing or its hash changed")
    profile = row[0]
    sources = profile.get("sources")
    bounded = (profile.get("collection_profile") or {}).get("decision") in {
        "bounded_balanced_openalex_arxiv_candidates",
        "bounded_compound_concept_candidates",
    }
    if sources != ["arxiv"] and not (
        bounded and sources in (["openalex"], ["arxiv", "openalex"])
    ):
        raise ValueError("Unsupported full-analysis source scope")
    return profile


def _write_profile(profile: dict) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False)
    try:
        handle.write(_canonical(profile))
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)
    finally:
        handle.close()


def _reuse_package(path: Path, profile: dict) -> dict | None:
    if not path.exists():
        return None
    manifest_path = path / "manifest.json"
    mission_path = path / "mission.json"
    if not manifest_path.is_file() or not mission_path.is_file():
        raise ValueError("Existing full-analysis package is incomplete")
    mission_text = mission_path.read_text(encoding="utf-8")
    mission = json.loads(mission_text)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mission != profile or hashlib.sha256(mission_text.encode()).hexdigest() != digest(profile):
        raise ValueError("Existing package belongs to a different execution profile")
    from saia.ingest import validate_collection_input
    validate_collection_input(path, manifest, mission, mission_text)
    audit = json.loads((path / manifest["audit_file"]).read_text(encoding="utf-8"))
    return {"package": str(path), "manifest": manifest, "audit": audit}


def _atomic_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != report:
            raise ValueError("Existing parent coverage report has different content")
        return
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _existing_normalize(mission_id: str, batch_id: int, query_version_id: str) -> int | None:
    from saia.canonical_text import VERSION as TEXT_POLICY_VERSION
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT run_id FROM analysis_run WHERE mission_id=%s AND query_version_id=%s "
            "AND kind='normalize' AND status='done' "
            "AND notes->>'collection_batch_id'=%s "
            "AND notes->'canonical_text_policy'->>'version'=%s ORDER BY run_id DESC LIMIT 1",
            (mission_id, query_version_id, str(batch_id), TEXT_POLICY_VERSION),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _existing_coverage(batch_id: int) -> tuple[int, dict] | None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT coverage_passport_id,evidence_payload FROM collection_coverage_passport "
            "WHERE batch_id=%s AND comparison_scope="
            "'within_frozen_snapshot_same_query_window'",
            (batch_id,),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _existing_quality(normalize_run_id: int) -> int | None:
    from saia.quality import POLICY_VERSION
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT generation_id FROM quality_generation WHERE normalize_run_id=%s "
            "AND policy_version=%s AND status='done' "
            "ORDER BY generation_id DESC LIMIT 1",
            (normalize_run_id, POLICY_VERSION),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _embedding_complete(normalize_run_id: int, generation_id: int,
                        model: str) -> bool:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*),count(e.work_id) FROM work w "
            "JOIN quality_snapshot q ON q.work_id=w.work_id AND q.generation_id=%s "
            "LEFT JOIN work_embedding e ON e.work_id=w.work_id AND e.model=%s "
            "WHERE w.run_id=%s AND q.decision='include'",
            (generation_id, model, normalize_run_id),
        )
        expected, actual = cur.fetchone()
    return expected > 0 and actual == expected


def _quality_counts(normalize_run_id: int, generation_id: int) -> dict[str, int]:
    """Count the exact quality input before starting the expensive stages."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT q.decision,count(*) FROM work w "
            "JOIN quality_snapshot q ON q.work_id=w.work_id AND q.generation_id=%s "
            "WHERE w.run_id=%s GROUP BY q.decision",
            (generation_id, normalize_run_id),
        )
        return {str(decision): count for decision, count in cur.fetchall()}


def _existing_cluster(mission_id: str, normalize_run_id: int,
                      generation_id: int, model: str, window_step: str,
                      backend: str) -> int | None:
    methodology_hash = methodology.load_default().config_hash
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT run_id FROM analysis_run WHERE mission_id=%s AND kind='cluster' "
            "AND status='done' AND upstream_run_id=%s AND embedding_model=%s "
            "AND window_step=%s AND clustering_scale=%s "
            "AND methodology_hash=%s "
            "AND notes->>'quality_generation_id'=%s "
            "AND notes->>'clustering_backend'=%s "
            "ORDER BY run_id DESC LIMIT 1",
            (mission_id, normalize_run_id, model, window_step,
             f"micro:{backend}", methodology_hash, str(generation_id), backend),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _existing_score(mission_id: str, cluster_run_id: int,
                    coverage_passport_id: int | None,
                    historical_novelty: dict | None = None) -> int | None:
    expected_diagnostic = (
        historical_novelty.get("diagnostic_payload_sha256")
        if historical_novelty else None
    )
    with db.connect() as conn, conn.cursor() as cur:
        sql = (
            "SELECT run_id FROM analysis_run WHERE mission_id=%s AND kind='score' "
            "AND status='done' AND upstream_run_id=%s "
            "AND notes->>'feature_code'='candidates-v7-optional-historical-background' "
        )
        params: list = [mission_id, cluster_run_id]
        if coverage_passport_id is None:
            sql += "AND notes->>'coverage_passport_id' IS NULL "
        else:
            sql += "AND notes->>'coverage_passport_id'=%s "
            params.append(str(coverage_passport_id))
        if expected_diagnostic is not None:
            sql += "AND notes->'historical_novelty'->>'diagnostic_payload_sha256'=%s "
            params.append(expected_diagnostic)
        else:
            # A score calculated with an older historical background must not
            # be reused when the current input cannot provide that background.
            sql += "AND COALESCE(notes->'historical_novelty', 'null'::jsonb)='null'::jsonb "
        sql += "ORDER BY run_id DESC LIMIT 1"
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def _score_historical_novelty(score_run_id: int) -> dict | None:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT notes->'historical_novelty' FROM analysis_run "
            "WHERE run_id=%s AND kind='score' AND status='done'",
            (score_run_id,),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def _load_hashed_json(path: Path) -> dict:
    from saia.package_observations import payload_hash
    payload = json.loads(path.read_text(encoding="utf-8"))
    claimed = payload.get("payload_sha256")
    if claimed != payload_hash({key: value for key, value in payload.items()
                               if key != "payload_sha256"}):
        raise ValueError(f"Historical novelty artifact hash mismatch: {path.name}")
    return payload


def _prepare_historical_novelty(profile: dict, audit: dict, cluster_run_id: int,
                                model: str, project_root: Path) -> dict:
    """Build/reuse a bounded background only when its scope is proven."""
    if not model.startswith("specter2/proximity@"):
        return {"status": "skipped", "reason": "embedding_model_is_not_specter2"}
    if audit.get("selection_engine") != "verified_thematic_target_pack":
        return {
            "status": "skipped",
            "reason": "collection_not_built_from_verified_thematic_target_pack",
        }
    selected = list(audit.get("selected_target_packs") or [])
    target_key = (
        selected[0] if len(selected) == 1 and isinstance(selected[0], str)
        else selected[0].get("target_key")
        if len(selected) == 1 and isinstance(selected[0], dict)
        else None
    )
    if not target_key:
        return {
            "status": "skipped",
            "reason": "historical_background_requires_one_proven_target_pack",
        }
    cache_dir_text = os.environ.get("SAIA_THEMATIC_ARXIV_CACHE_DIR")
    packs_dir_text = os.environ.get("SAIA_THEMATIC_ARXIV_PACKS_DIR")
    if not cache_dir_text or not packs_dir_text:
        return {"status": "skipped", "reason": "thematic_cache_paths_not_configured"}
    plan = profile["controlled_search_plan"]
    cutoff = plan["date_from"]
    terms = list(plan.get("included_terms") or [])
    if not terms:
        return {"status": "skipped", "reason": "controlled_plan_has_no_terms"}
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM topic WHERE run_id=%s", (cluster_run_id,))
        current_topics = cur.fetchone()[0]
    if current_topics < 5:
        return {
            "status": "skipped",
            "reason": "fewer_than_5_current_peer_topics_for_novelty_percentile",
            "current_topics": current_topics,
        }

    cache_dir, packs_dir = Path(cache_dir_text), Path(packs_dir_text)
    identity = digest({
        "cache_manifest_sha256": audit["cache_manifest_sha256"],
        "target_pack_manifest_sha256": audit["target_pack_manifest_sha256"],
        "target_key": target_key, "cutoff": cutoff,
        "included_terms": terms, "exclusions": list(plan.get("exclusions") or []),
        "matching_version": plan.get("matching_version", "literal-phrase-0.4.6"),
        "model": model,
    })
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "-", target_key)[:80]
    background_dir = project_root / "data" / "processed" / (
        f"historical-background-auto-{safe_target}-{identity[:16]}"
    )
    if background_dir.exists():
        from saia.historical_background import load
        background_manifest, _ = load(background_dir)
    else:
        # Preflight distinguishes an expected capacity gap from corruption.
        from saia.historical_background import build, historical_rows
        from saia.thematic_arxiv_cache import load_cache, load_target_packs
        cache_manifest, _ = load_cache(cache_dir)
        packs_manifest = load_target_packs(packs_dir, cache_manifest["payload_sha256"])
        target = next((item for item in packs_manifest["targets"]
                       if item["target_key"] == target_key), None)
        if target is None:
            raise ValueError("Selected thematic target pack is absent")
        try:
            rows, _counts = historical_rows(
                packs_dir / target["file"], date.fromisoformat(cutoff),
                max_records=2000,
                plan={"included_terms": terms,
                      "exclusions": list(plan.get("exclusions") or [])},
            )
        except ValueError as error:
            message = str(error)
            if ("exceeds max_records" in message
                    or "No pre-cutoff" in message):
                return {"status": "skipped", "reason": message}
            raise
        if len(rows) < 25:
            return {
                "status": "skipped",
                "reason": "fewer_than_25_relevant_pre_period_publications",
                "relevant_pre_period_publications": len(rows),
            }
        try:
            background_manifest = build(
                cache_dir, packs_dir, target_key, date.fromisoformat(cutoff),
                background_dir, max_records=2000, batch_size=8, max_rss_gib=8,
                included_terms=terms,
                exclusions=list(plan.get("exclusions") or []),
            )
        except ValueError as error:
            if str(error) == "Historical clustering produced no topic anchors":
                return {"status": "skipped", "reason": str(error)}
            raise
        if background_manifest.get("status") != "passed":
            return {
                "status": "skipped",
                "reason": "historical_background_exceeded_runtime_resource_limit",
            }

    report_root = project_root / "reports" / "generated"
    diagnostic_path = report_root / (
        f"historical-novelty-cluster-{cluster_run_id}-{identity[:16]}.json"
    )
    if diagnostic_path.exists():
        diagnostic = _load_hashed_json(diagnostic_path)
    else:
        from saia.historical_novelty_diagnostic import diagnose
        try:
            diagnostic = diagnose(
                cache_dir, packs_dir, background_dir, cluster_run_id, diagnostic_path
            )
        except ValueError as error:
            if str(error) == "Historical background has no topic anchors":
                return {"status": "skipped", "reason": str(error)}
            raise
    sensitivity_path = report_root / (
        f"historical-novelty-sensitivity-cluster-{cluster_run_id}-{identity[:16]}.json"
    )
    if sensitivity_path.exists():
        sensitivity = _load_hashed_json(sensitivity_path)
    else:
        from saia.historical_novelty_sensitivity import run as sensitivity_run
        try:
            sensitivity = sensitivity_run(
                background_dir, diagnostic_path, cluster_run_id, sensitivity_path,
                min_topic_sizes=[5, 10, 15], seeds=[42, 43, 44],
            )
        except ValueError as error:
            if (str(error).startswith("No anchors for sensitivity variant")
                    or str(error) == (
                        "Sensitivity variants require available novelty percentiles"
                    )):
                return {"status": "skipped", "reason": str(error)}
            raise
    return {
        "status": "ready",
        "background_dir": str(background_dir),
        "background_manifest_payload_sha256": background_manifest["payload_sha256"],
        "diagnostic_path": str(diagnostic_path),
        "diagnostic_payload_sha256": diagnostic["payload_sha256"],
        "sensitivity_path": str(sensitivity_path),
        "sensitivity_payload_sha256": sensitivity["payload_sha256"],
        "relevant_pre_period_publications": background_manifest["counts"][
            "embedded_eligible"
        ],
        "background_topics": background_manifest["counts"]["background_topics"],
    }


def _parent_audit(mirror: Path, package: Path) -> dict:
    from saia.arxiv_parent_corpus import audit as audit_full

    if os.environ.get("SAIA_ARXIV_PARENT_INDEX_ENABLED") != "1":
        return audit_full(mirror, package / "mission.json", package / "manifest.json")
    index_dir = os.environ.get("SAIA_ARXIV_TRIGRAM_INDEX_DIR")
    if not index_dir:
        raise ValueError("Guarded parent audit requires a configured arXiv index")
    from saia.arxiv_parent_index_audit import IndexAuditIneligible, audit as audit_guarded
    try:
        return audit_guarded(mirror_dir=mirror,
                             mission_path=package / "mission.json",
                             package_manifest_path=package / "manifest.json",
                             index_dir=Path(index_dir))
    except IndexAuditIneligible:
        return audit_full(mirror, package / "mission.json", package / "manifest.json")


def run_job(payload: dict, *,
            progress: Callable[[str, str, dict], None] | None = None,
            cancel_check: Callable[[], bool] | None = None) -> dict:
    """Execute or safely reuse every frozen stage and return a compact result."""
    progress = progress or (lambda _stage, _state, _details: None)
    cancel_check = cancel_check or (lambda: False)

    def stage(name: str, state: str, details: dict | None = None) -> None:
        progress(name, state, details or {})

    def check_cancel() -> None:
        if cancel_check():
            raise FullAnalysisCancelled("Задача отменена между стадиями анализа")

    if payload.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("Unsupported full-analysis job contract")
    mission_id = payload["mission_id"]
    query_id = payload["execution_query_version_id"]
    profile = _load_execution_profile(query_id, payload["execution_profile_sha256"])
    bounded_mode = (profile.get("collection_profile") or {}).get("decision") in {
        "bounded_balanced_openalex_arxiv_candidates",
        "bounded_compound_concept_candidates",
    }
    if payload.get("source_scope") != profile.get("sources"):
        raise ValueError("Full-analysis source scope differs from frozen profile")
    if payload.get("request_sha256") != digest({k: v for k, v in payload.items()
                                                if k != "request_sha256"}):
        raise ValueError("Full-analysis request hash mismatch")
    mirror = os.environ.get("SAIA_ARXIV_MIRROR_DIR")
    if not mirror and not bounded_mode:
        raise ValueError("SAIA_ARXIV_MIRROR_DIR is required for full analysis")
    project_root = Path(__file__).resolve().parents[1]
    root = Path(os.environ.get("SAIA_FULL_ANALYSIS_ROOT") or
                project_root / "data" / "raw" / "analysis-jobs")
    package = root / f"{mission_id}-{payload['request_sha256'][:16]}"
    report_path = project_root / "reports" / "generated" / (
        f"{mission_id}-{payload['request_sha256'][:16]}-parent-arxiv.json"
    )

    with db.connect() as lock_conn, lock_conn.cursor() as lock_cur:
        lock_cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 6417))", (mission_id,))
        try:
            check_cancel()
            package_result = _reuse_package(package, profile)
            if package_result is None:
                stage("collect", "started", {"source_scope": profile["sources"]})
                if bounded_mode:
                    from saia.bounded_discovery_package import build_package
                    from saia.jobs import read as read_job
                    source_id = profile["collection_profile"]["provenance"][
                        "balanced_discovery_job_id"
                    ]
                    package_result = build_package(
                        profile, package, source_job=read_job(source_id)
                    )
                else:
                    mission_file = _write_profile(profile)
                    try:
                        from saia.controlled_collection import build_package
                        package_result = build_package(
                            mission_file, mirror, package,
                            max_records=int(payload["max_records"]),
                        )
                    finally:
                        mission_file.unlink(missing_ok=True)
                stage("collect", "succeeded", {
                    "selected_records": package_result["audit"]["selected_records"],
                    "inventory_rows": package_result["audit"].get("inventory_rows"),
                })
            else:
                stage("collect", "reused", {
                    "selected_records": package_result["audit"]["selected_records"],
                })

            check_cancel()
            stage("ingest", "started", {})
            from saia.ingest import ingest
            ingestion = ingest(package, verbose=False)
            batch_id = ingestion["batch_id"]
            stage("ingest", "reused" if ingestion.get("already_sealed") else "succeeded",
                  {"batch_id": batch_id, "records": ingestion.get("records", 0)})

            check_cancel()
            existing_coverage = None if bounded_mode else _existing_coverage(batch_id)
            if bounded_mode:
                passport_id = None
                coverage_report = {"counts": {}, "coverage": {
                    "temporal_comparable": None,
                    "reason": "bounded_balanced_source_selection",
                }}
                stage("parent_coverage", "skipped", {
                    "reason": "bounded_nonrepresentative_scientific_corpus",
                })
            elif existing_coverage is not None:
                passport_id, coverage_report = existing_coverage
                _atomic_report(report_path, coverage_report)
                stage("parent_coverage", "reused", {
                    "report": str(report_path),
                    "coverage_passport_id": passport_id,
                })
            elif report_path.exists():
                coverage_report = json.loads(report_path.read_text(encoding="utf-8"))
                from saia.coverage_passport import register
                coverage = register(package, report_path)
                passport_id = coverage["coverage_passport_id"]
                stage("parent_coverage", "reused", {
                    "report": str(report_path),
                    "coverage_passport_id": passport_id,
                })
            else:
                stage("parent_coverage", "started", {})
                coverage_report = _parent_audit(Path(mirror), package)
                _atomic_report(report_path, coverage_report)
                from saia.coverage_passport import register
                coverage = register(package, report_path)
                passport_id = coverage["coverage_passport_id"]
                stage("parent_coverage", "succeeded", {
                    "parent_native_ids": coverage_report["counts"]["parent_native_ids_in_period"],
                    "audit_version": coverage_report["version"],
                    "coverage_passport_id": passport_id,
                })

            check_cancel()
            normalize_run_id = _existing_normalize(mission_id, batch_id, query_id)
            if normalize_run_id is None:
                stage("normalize", "started", {"batch_id": batch_id})
                from saia.normalize import normalize
                normalized = normalize(
                    mission_id, verbose=False, collection_batch_id=batch_id
                )
                normalize_run_id = normalized["run_id"]
                stage("normalize", "succeeded", {
                    "run_id": normalize_run_id, "processed": normalized["processed"],
                })
            else:
                stage("normalize", "reused", {"run_id": normalize_run_id})

            check_cancel()
            generation_id = _existing_quality(normalize_run_id)
            if generation_id is None:
                stage("quality", "started", {"normalize_run_id": normalize_run_id})
                from saia.quality import evaluate_mission
                quality = evaluate_mission(mission_id, normalize_run_id)
                generation_id = quality["quality_generation_id"]
                stage("quality", "succeeded", {
                    "quality_generation_id": generation_id,
                })
            else:
                stage("quality", "reused", {"quality_generation_id": generation_id})

            check_cancel()
            quality_counts = _quality_counts(normalize_run_id, generation_id)
            if quality_counts.get("include", 0) == 0:
                # A bounded search can return records that all fail the exact
                # topic check.  This is an empty evidence result, not a broken
                # embedding model or a negative finding about the technology.
                stage("embed", "skipped", {"reason": "no_quality_included_works"})
                stage("cluster", "skipped", {"reason": "no_quality_included_works"})
                stage("score", "skipped", {"reason": "no_quality_included_works"})
                stage("triage", "succeeded", {"shown": 0, "statuses": {}})
                audit = package_result["audit"]
                return {
                    "contract_version": CONTRACT_VERSION,
                    "result_role": RESULT_ROLE,
                    "input_status": "no_eligible_publications",
                    "mission_id": mission_id,
                    "base_query_version_id": payload["base_query_version_id"],
                    "execution_query_version_id": query_id,
                    "package": str(package),
                    "package_manifest_sha256": hashlib.sha256(
                        (package / "manifest.json").read_bytes()
                    ).hexdigest(),
                    "collection": {
                        "batch_id": batch_id,
                        "selected_records": audit["selected_records"],
                        "scanned_rows": audit["scanned_rows"],
                        "source_scope": profile["sources"],
                        "source_records": audit.get("source_records"),
                        "complete_within_pinned_mirror_exact_predicate": (
                            False if bounded_mode else True
                        ),
                        "retrieval_recall_evaluated": False,
                    },
                    "quality": {"counts": quality_counts},
                    "coverage": {
                        "coverage_passport_id": passport_id,
                        "parent_report": None if bounded_mode else str(report_path),
                        "world_science_coverage_complete": None,
                        "temporal_comparability": None if bounded_mode else True,
                    },
                    "runs": {
                        "normalize": normalize_run_id,
                        "quality_generation": generation_id,
                        "cluster": None,
                        "score": None,
                        "embedding_model": None,
                    },
                    "cards": {
                        "all": 0, "shown": 0, "statuses": {}, "candidate_ids": [],
                    },
                    "limitations": [
                        "Все найденные публикации исключены проверкой соответствия теме. "
                        "Это не доказывает отсутствие исследований или слабых сигналов; "
                        "нужен новый поисковый план или расширение источников."
                    ],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

            check_cancel()
            model = expected_model_name()
            if _embedding_complete(normalize_run_id, generation_id, model):
                stage("embed", "reused", {"model": model})
            else:
                stage("embed", "started", {"model": model})
                from saia.embed import embed_mission
                embedded = embed_mission(
                    mission_id, quality_generation_id=generation_id
                )
                model = embedded["model"]
                if not _embedding_complete(normalize_run_id, generation_id, model):
                    raise ValueError("Embedding stage did not cover the exact quality input")
                stage("embed", "succeeded", {
                    "model": model, "computed": embedded["computed"],
                    "reused_exact_text": embedded["reused_exact_text"],
                    "skipped": embedded["skipped"],
                })

            check_cancel()
            from saia import methodology
            window_step = methodology.load_default().window_step
            # For the current product result, discover topic membership once
            # on the complete bounded corpus and measure the fixed topics over
            # quarterly snapshots.  Re-clustering only 10-20 papers in every
            # quarter discarded most real lines as noise.  This mode is a
            # current-period reconstruction and must never be presented as a
            # leakage-free retrospective backtest.
            clustering_backend = "global_bertopic"
            cluster_run_id = _existing_cluster(
                mission_id, normalize_run_id, generation_id, model, window_step,
                clustering_backend,
            )
            if cluster_run_id is None:
                stage("cluster", "started", {
                    "backend": clustering_backend, "step": window_step,
                    "temporal_definition_scope": (
                        "full_period_current_reconstruction_not_backtest"
                    ),
                })
                from saia.cluster import analyze
                clustered = analyze(
                    mission_id, generation_id, model,
                    backend=clustering_backend, step=window_step, scale="micro",
                    current_period_reconstruction=True,
                )
                cluster_run_id = clustered["run_id"]
                stage("cluster", "succeeded", {"run_id": cluster_run_id})
            else:
                stage("cluster", "reused", {"run_id": cluster_run_id})

            check_cancel()
            stage("historical_novelty", "started", {"cluster_run_id": cluster_run_id})
            historical_novelty = (
                {"status": "skipped", "reason": "bounded_nonrepresentative_corpus"}
                if bounded_mode else _prepare_historical_novelty(
                    profile, package_result["audit"], cluster_run_id, model,
                    project_root,
                )
            )
            stage(
                "historical_novelty",
                "succeeded" if historical_novelty["status"] == "ready" else "skipped",
                historical_novelty,
            )

            check_cancel()
            score_run_id = _existing_score(
                mission_id, cluster_run_id, passport_id,
                historical_novelty if historical_novelty["status"] == "ready" else None,
            )
            if score_run_id is None:
                stage("score", "started", {
                    "cluster_run_id": cluster_run_id,
                    "historical_novelty": historical_novelty["status"],
                })
                from saia.candidates import build_candidates
                scored = build_candidates(
                    mission_id, cluster_run_id,
                    historical_novelty_report=(
                        Path(historical_novelty["diagnostic_path"])
                        if historical_novelty["status"] == "ready" else None
                    ),
                    historical_novelty_sensitivity=(
                        Path(historical_novelty["sensitivity_path"])
                        if historical_novelty["status"] == "ready" else None
                    ),
                )
                score_run_id = scored["run_id"]
                stage("score", "succeeded", {"run_id": score_run_id})
            else:
                saved_historical_novelty = _score_historical_novelty(score_run_id)
                if saved_historical_novelty:
                    historical_novelty = {
                        "status": "reused_from_existing_score",
                        **saved_historical_novelty,
                    }
                stage("score", "reused", {"run_id": score_run_id})

            check_cancel()
            from saia.candidates import export_cards
            from saia.triage import build_queue
            cards = export_cards(mission_id, score_run_id)
            triage = build_queue(cards, int(payload["top_n"]))
            status_counts = triage["counts"]["statuses"]
            stage("triage", "succeeded", {
                "shown": triage["counts"]["shown"], "statuses": status_counts,
            })
            audit = package_result["audit"]
            return {
                "contract_version": CONTRACT_VERSION,
                "result_role": RESULT_ROLE,
                "mission_id": mission_id,
                "base_query_version_id": payload["base_query_version_id"],
                "execution_query_version_id": query_id,
                "package": str(package),
                "package_manifest_sha256": hashlib.sha256(
                    (package / "manifest.json").read_bytes()
                ).hexdigest(),
                "collection": {
                    "batch_id": batch_id,
                    "selected_records": audit["selected_records"],
                    "scanned_rows": audit["scanned_rows"],
                    "source_scope": profile["sources"],
                    "source_records": audit.get("source_records"),
                    "complete_within_pinned_mirror_exact_predicate": (
                        False if bounded_mode else True
                    ),
                    "retrieval_recall_evaluated": False,
                },
                "coverage": {
                    "coverage_passport_id": passport_id,
                    "parent_report": None if bounded_mode else str(report_path),
                    "parent_native_ids_in_period": coverage_report["counts"].get(
                        "parent_native_ids_in_period"
                    ),
                    "world_science_coverage_complete": None,
                    "temporal_comparability": None if bounded_mode else True,
                },
                "runs": {
                    "normalize": normalize_run_id,
                    "quality_generation": generation_id,
                    "cluster": cluster_run_id,
                    "score": score_run_id,
                    "embedding_model": model,
                },
                "cards": {
                    "all": triage["counts"]["all_cards"],
                    "shown": triage["counts"]["shown"],
                    "statuses": status_counts,
                    "candidate_ids": [item["candidate_id"] for item in triage["queue"]],
                },
                "historical_novelty": historical_novelty,
                "api": {
                    "triage": f"/triage/{mission_id}?score_run_id={score_run_id}&limit={payload['top_n']}",
                    "signals": f"/signals/{mission_id}?score_run_id={score_run_id}",
                },
                "limitations": ([
                    "OpenAlex и arXiv участвуют в построении тем, но ограниченная выдача не даёт сопоставимого временного ряда; рост и новизна остаются непроверенными.",
                    "Полнота нахождения независимых контрольных тем этим запуском не оценивалась.",
                    "Результат — очередь научной проверки, а не прогноз рынка и не список подтверждённых трендов.",
                ] if bounded_mode else [
                    "Полный корпус этой задачи ограничен локальным arXiv; OpenAlex не заявлен как полное покрытие.",
                    "Полнота нахождения независимых контрольных тем этим запуском не оценивалась.",
                    "Результат — очередь научной проверки, а не прогноз рынка и не список подтверждённых трендов.",
                ]),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            lock_cur.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 6417))",
                             (mission_id,))
