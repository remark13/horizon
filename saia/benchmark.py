"""Слепой ретротест: проверка результата без подмешивания известного ответа.

Контрольный список публикаций читается только после кластеризации. Он не
участвует в сборе корпуса, построении эмбеддингов, кластеризации, связывании
тем или расчёте признаков. Поэтому отрицательный результат так же допустим и
информативен, как положительный.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from saia import db, runs


ROOT = Path(__file__).resolve().parents[1]


def load_benchmark_config(mission_id: str) -> dict[str, Any]:
    path = ROOT / "missions" / f"{mission_id}.json"
    if not path.exists():
        raise ValueError(f"нет файла миссии {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    benchmark = payload.get("benchmark")
    if not benchmark:
        raise ValueError(f"для миссии {mission_id} не описан benchmark")
    return benchmark


def canonical_cluster_payload(cur, run_id: int) -> list[dict[str, Any]]:
    """Представление разбиения, не зависящее от последовательных topic_id."""
    cur.execute(
        """
        SELECT t.topic_id, t.first_window, t.last_window,
               tm.window_key, tm.work_id
        FROM topic t
        LEFT JOIN topic_membership tm USING (topic_id)
        WHERE t.run_id = %s
        ORDER BY t.topic_id, tm.window_key, tm.work_id
        """,
        (run_id,),
    )
    topics: dict[int, dict[str, Any]] = {}
    for topic_id, first, last, window, work_id in cur.fetchall():
        item = topics.setdefault(topic_id, {
            "first_window": first, "last_window": last, "memberships": [],
        })
        if work_id is not None:
            item["memberships"].append([window, int(work_id)])

    cur.execute(
        """
        SELECT ts.topic_id, ts.window_key, ts.doc_count, ts.popularity,
               ts.slope, ts.popularity_percentile, ts.lifecycle_class
        FROM topic_snapshot ts JOIN topic t USING (topic_id)
        WHERE t.run_id = %s
        ORDER BY ts.topic_id, ts.window_key
        """,
        (run_id,),
    )
    for topic_id, window, count, popularity, slope, percentile, lifecycle in cur.fetchall():
        topics[topic_id].setdefault("snapshots", []).append({
            "window": window,
            "doc_count": count,
            "popularity": round(float(popularity), 8),
            "slope": round(float(slope or 0.0), 8),
            "percentile": None if percentile is None else round(float(percentile), 8),
            "lifecycle": lifecycle,
        })
    return sorted(topics.values(), key=lambda item: json.dumps(item, sort_keys=True))


def cluster_signature(payload: list[dict[str, Any]]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recovery_metrics(target_topics: list[int | None], eligible_count: int,
                     assigned_count: int, min_same_topic: int,
                     min_recall: float,
                     topic_sizes: dict[int, int] | None = None,
                     min_target_share: float = 0.1) -> dict[str, Any]:
    counts = Counter(topic for topic in target_topics if topic is not None)
    max_same = max(counts.values(), default=0)
    dominant_topic = min(
        (topic for topic, count in counts.items() if count == max_same),
        default=None,
    )
    dominant_size = (
        (topic_sizes or {}).get(dominant_topic)
        if dominant_topic is not None else None
    )
    dominant_share = (
        max_same / dominant_size if dominant_size else None
    )
    recall = assigned_count / eligible_count if eligible_count else None
    dominant_recall = max_same / eligible_count if eligible_count else None
    distinct = bool(
        eligible_count
        and max_same >= min_same_topic
        and dominant_recall is not None
        and dominant_recall >= min_recall
        and dominant_share is not None
        and dominant_share >= min_target_share
    )
    return {
        "eligible_target_works": eligible_count,
        "assigned_target_works": assigned_count,
        "eligible_target_recall": None if recall is None else round(recall, 4),
        'dominant_topic_target_recall': None if dominant_recall is None else round(dominant_recall, 4),
        "max_target_works_in_one_topic": max_same,
        "dominant_topic_id": dominant_topic,
        "dominant_topic_size": dominant_size,
        "dominant_target_share": (
            None if dominant_share is None else round(dominant_share, 4)
        ),
        "target_topic_distribution": {
            str(topic): count for topic, count in sorted(counts.items())
        },
        "acceptance": {
            "min_target_works_same_topic": min_same_topic,
            "min_eligible_target_recall": min_recall,
            "min_target_share_in_dominant_topic": min_target_share,
        },
        "detected_as_distinct_topic": distinct,
    }


def _latest_quality_lateral(alias: str = "w", generation_id: int | None = None) -> str:
    if generation_id is not None:
        return (f'LEFT JOIN quality_snapshot q ON q.work_id = {alias}.work_id '
                f'AND q.generation_id = {int(generation_id)}')
    return f"""
        LEFT JOIN LATERAL (
            SELECT q0.decision, q0.flags, q0.reasons
            FROM work_quality q0 WHERE q0.work_id = {alias}.work_id
            ORDER BY q0.evaluated_at DESC, q0.policy_version DESC LIMIT 1
        ) q ON true
    """


def benchmark_targets(benchmark: dict[str, Any]) -> list[dict[str, str]]:
    """Нормализовать контрольные идентификаторы, сохранив старый формат."""
    explicit = benchmark.get("target_identifiers")
    if explicit:
        return [
            {"kind": str(item["kind"]).lower(), "value": str(item["value"]).lower()}
            for item in explicit
        ]
    return [
        {"kind": "arxiv", "value": str(value)}
        for value in benchmark.get("target_arxiv_ids", [])
    ]


def identifier_url(kind: str, value: str) -> str:
    if kind == "arxiv":
        return f"https://arxiv.org/abs/{value}"
    if kind == "doi":
        return f"https://doi.org/{value}"
    if kind == "openalex":
        return f"https://openalex.org/{value.upper()}"
    return value


def interpret_target_result(
    target_rows: dict[str, dict[str, Any]],
    recovery: dict[str, Any],
    target_label: str,
) -> dict[str, Any]:
    """Разделить сбой охвата корпуса и сбой тематического выделения.

    Контрольный набор применяется только после расчёта, поэтому это объяснение
    результата, а не способ расширить корпус известными ответами.
    """
    total = len(target_rows)
    found = sum(bool(item.get("found_in_corpus")) for item in target_rows.values())
    coverage = round(found / total, 4) if total else None
    collection = {
        "control_publications": total,
        "found_in_corpus": found,
        "missing_from_corpus": total - found,
        "control_set_recall": coverage,
    }
    if found < total:
        return {
            "failure_stage": "collection_coverage",
            "collection_coverage": collection,
            "conclusion": (
                f"{target_label} не обнаружен как самостоятельный слабый сигнал. "
                f"Первый установленный сбой — охват корпуса: найдено {found} из {total} "
                "контрольных публикаций. Поэтому этот прогон не позволяет приписать "
                "отрицательный результат одной кластеризации."
            ),
            "next_step": (
                "Повысить полноту входа без использования названия контрольного сигнала: "
                "взять замороженный категорийный родительский корпус или проверить "
                "recall-ориентированное расширение исходного запроса на независимом "
                "development-наборе. Затем повторить тот же контроль постфактум."
            ),
        }
    if not recovery["detected_as_distinct_topic"]:
        return {
            "failure_stage": "topic_recovery",
            "collection_coverage": collection,
            "conclusion": (
                f"{target_label} не обнаружен как самостоятельный слабый сигнал в "
                "ретропрогоне. Контрольные статьи присутствуют в корпусе, но не "
                "собраны в одну достаточно самостоятельную тему."
            ),
            "next_step": (
                "Проверить независимый гибридный генератор кандидатов: устойчивые "
                "термины по окнам, граф совместной встречаемости и семантическое "
                "объединение — без включения названия контрольного сигнала в поиск."
            ),
        }
    return {
        "failure_stage": None,
        "collection_coverage": collection,
        "conclusion": (
            f"{target_label} восстановлен как самостоятельная тема. Дальше отдельно "
            "проверяется прохождение правил слабого сигнала."
        ),
        "next_step": (
            "Проверить переносимость результата на зарезервированных темах и не "
            "менять production-пороги без отдельного калибровочного эксперимента."
        ),
    }


def build_benchmark(mission_id: str, score_run_id: int | None = None) -> dict[str, Any]:
    benchmark = load_benchmark_config(mission_id)
    acceptance = benchmark["acceptance"]
    targets = benchmark_targets(benchmark)

    with db.connect() as conn, conn.cursor() as cur:
        score_run_id = score_run_id or runs.current_run_id(cur, mission_id, "score")
        cur.execute("SELECT upstream_run_id FROM analysis_run WHERE run_id = %s "
                    "AND mission_id = %s AND kind = 'score' AND status = 'done'", (score_run_id, mission_id))
        row = cur.fetchone()
        if not row:
            raise ValueError('Нет завершённого score-прогона данной миссии.')
        cluster_run_id = row[0]

        cur.execute(
            "SELECT upstream_run_id, embedding_model, clustering_scale, notes, methodology_hash, "
            "window_step, code_version FROM analysis_run WHERE run_id = %s AND mission_id = %s "
            "AND kind = 'cluster' AND status = 'done'", (cluster_run_id, mission_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError('Входной кластер отсутствует или не завершён.')
        normalize_run_id, model, scale, cluster_notes, method_hash, step, code = row
        quality_generation_id = (cluster_notes or {}).get('quality_generation_id')
        cur.execute("SELECT notes FROM analysis_run WHERE run_id = %s AND mission_id = %s "
                    "AND kind = 'normalize' AND status = 'done'", (normalize_run_id, mission_id))
        root_row = cur.fetchone()
        if not root_row:
            raise ValueError('Входной корпус отсутствует или не завершён.')
        collection_batch_id = (root_row[0] or {}).get('collection_batch_id')
        cur.execute(
            "SELECT upstream_run_id FROM analysis_run WHERE run_id = %s",
            (score_run_id,),
        )
        score_upstream = cur.fetchone()[0]
        if score_upstream != cluster_run_id:
            raise ValueError("текущие прогоны образуют несогласованную цепочку происхождения")

        period_from, as_of, period_origin = runs.analysis_period(cur, normalize_run_id)
        period_from = period_from or date.min

        # Публикации-эталоны появляются только здесь, после всех расчётов.
        target_rows: dict[str, dict[str, Any]] = {}
        for target in targets:
            kind, value = target["kind"], target["value"]
            target_key = f"{kind}:{value}"
            cur.execute(
                f"""
                SELECT w.work_id, w.canonical_title, w.effective_date,
                       q.decision, q.flags, q.reasons,
                       ct.topic_id, ct.label, sc.status, sc.emergence_score
                FROM identifier i JOIN work w USING (work_id)
                {_latest_quality_lateral('w', quality_generation_id)}
                LEFT JOIN LATERAL (
                    SELECT t.topic_id, t.label, tm.window_key
                    FROM topic_membership tm JOIN topic t USING (topic_id)
                    WHERE tm.work_id = w.work_id AND t.run_id = %s
                    ORDER BY tm.window_key DESC LIMIT 1
                ) ct ON true
                LEFT JOIN signal_candidate sc
                  ON sc.topic_id = ct.topic_id AND sc.run_id = %s
                WHERE i.run_id = %s AND i.kind = %s AND lower(i.value) = %s
                LIMIT 1
                """,
                (cluster_run_id, score_run_id, normalize_run_id, kind, value),
            )
            row = cur.fetchone()
            if not row:
                target_rows[target_key] = {
                    "identifier_kind": kind, "identifier_value": value,
                    "arxiv_id": value if kind == "arxiv" else None,
                    "found_in_corpus": False,
                    "source_url": identifier_url(kind, value),
                }
                continue
            (work_id, title, effective, decision, flags, reasons, topic_id,
             topic_label, status, score) = row
            eligible = decision == "include" and period_from <= effective < as_of
            target_rows[target_key] = {
                "identifier_kind": kind, "identifier_value": value,
                "arxiv_id": value if kind == "arxiv" else None,
                "found_in_corpus": True,
                "work_id": work_id, "title": title,
                "effective_date": effective.isoformat(),
                "quality_decision": decision,
                "quality_flags": flags or {}, "quality_reasons": list(reasons or []),
                "eligible_for_clustering": eligible,
                "assigned_to_topic": topic_id is not None,
                "topic_id": topic_id, "topic_label": topic_label,
                "candidate_status": status, "emergence_score": score,
                "source_url": identifier_url(kind, value),
            }

        eligible = [item for item in target_rows.values()
                    if item.get("eligible_for_clustering")]
        assigned = [item for item in eligible if item.get("assigned_to_topic")]
        topic_sizes: dict[int, int] = {}
        for topic_id in {item["topic_id"] for item in assigned}:
            cur.execute(
                "SELECT count(*) FROM topic_membership WHERE topic_id = %s",
                (topic_id,),
            )
            topic_sizes[topic_id] = cur.fetchone()[0]
        recovery = recovery_metrics(
            [item.get("topic_id") for item in eligible], len(eligible), len(assigned),
            int(acceptance["min_target_works_same_topic"]),
            float(acceptance["min_eligible_target_recall"]),
            topic_sizes,
            float(acceptance["min_target_share_in_dominant_topic"]),
        )
        dominant_status = next((item.get('candidate_status') for item in assigned
                                if item['topic_id'] == recovery['dominant_topic_id']), None)
        qualified_signal = recovery['detected_as_distinct_topic'] and dominant_status == 'forming'

        cur.execute("SELECT count(*) FROM topic WHERE run_id = %s", (cluster_run_id,))
        topic_count = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM topic_membership tm JOIN topic t USING (topic_id) "
            "JOIN work w USING (work_id) WHERE t.run_id = %s "
            "AND (w.effective_date < %s OR w.effective_date >= %s)",
            (cluster_run_id, period_from, as_of),
        )
        temporal_leaks = cur.fetchone()[0]
        cur.execute(
            f"""
            SELECT count(*) FROM topic_membership tm
            JOIN topic t USING (topic_id) JOIN work w USING (work_id)
            {_latest_quality_lateral('w', quality_generation_id)}
            WHERE t.run_id = %s AND COALESCE(q.decision, 'include') <> 'include'
            """,
            (cluster_run_id,),
        )
        noninclude_memberships = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM signal_candidate WHERE run_id = %s "
            "AND status = 'forming' AND COALESCE((metrics->'observed'->>'windows_present')::int, 0) <= 1",
            (score_run_id,),
        )
        forming_single_window = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM signal_candidate WHERE run_id = %s "
            "AND status = 'forming' AND (metrics->'observed'->>'maturity_percentile')::float > 80",
            (score_run_id,),
        )
        forming_too_mature = cur.fetchone()[0]

        # Повторяемость сравнивается с предыдущим завершённым прогоном с тем
        # же входом и настройками. Из отпечатка исключены topic_id и текстовые
        # ярлыки: они не меняют состав тематических линий.
        cur.execute(
            """
            SELECT run_id, embedding_model, clustering_scale, notes, methodology_hash, window_step, code_version
            FROM analysis_run
            WHERE mission_id = %s AND kind = 'cluster' AND status = 'done'
              AND upstream_run_id = %s AND run_id <> %s
            ORDER BY finished_at DESC, run_id DESC
            """,
            (mission_id, normalize_run_id, cluster_run_id),
        )
        previous_run_id = None
        current_backend = (
            (cluster_notes or {}).get("clustering_backend")
            or (cluster_notes or {}).get("backend")
        )
        for candidate_run, candidate_model, candidate_scale, candidate_notes, candidate_hash, candidate_step, candidate_code in cur.fetchall():
            if (candidate_model == model and candidate_scale == scale
                    and candidate_hash == method_hash and candidate_step == step and candidate_code == code
                    and quality_generation_id is not None
                    and (candidate_notes or {}).get('quality_generation_id') == quality_generation_id
                    and (candidate_notes or {}).get('runtime') == (cluster_notes or {}).get('runtime')
                    and ((candidate_notes or {}).get("clustering_backend")
                         or (candidate_notes or {}).get("backend")) == current_backend):
                previous_run_id = candidate_run
                break
        current_signature = cluster_signature(canonical_cluster_payload(cur, cluster_run_id))
        previous_signature = (
            cluster_signature(canonical_cluster_payload(cur, previous_run_id))
            if previous_run_id is not None else None
        )
        repeatable = (
            current_signature == previous_signature
            if previous_signature is not None else None
        )

        cur.execute(
            "SELECT status, count(*) FROM signal_candidate WHERE run_id = %s GROUP BY status",
            (score_run_id,),
        )
        statuses = {status: count for status, count in cur.fetchall()}
        cur.execute(
            """
            SELECT ts.window_key, count(*) FILTER (WHERE ts.doc_count > 0),
                   sum(ts.doc_count),
                   count(*) FILTER (WHERE ts.lifecycle_class = 'weak')
            FROM topic_snapshot ts JOIN topic t USING (topic_id)
            WHERE t.run_id = %s GROUP BY ts.window_key ORDER BY ts.window_key
            """,
            (cluster_run_id,),
        )
        windows = [
            {"window": window, "active_topics": active, "clustered_documents": docs,
             "weak_lifecycle_topics": weak}
            for window, active, docs, weak in cur.fetchall()
        ]
        cur.execute(
            """
            SELECT rr.source, count(*)
            FROM raw_record rr
            JOIN collection_batch_snapshot cbs USING (snapshot_id)
            JOIN collection_batch cb USING (batch_id)
            WHERE cbs.batch_id = %s AND cb.mission_id = %s AND cb.status = 'complete'
            GROUP BY rr.source
            """,
            (collection_batch_id, mission_id),
        )
        raw_counts = {source: count for source, count in cur.fetchall()}
        cur.execute("SELECT count(*) FROM work WHERE run_id = %s", (normalize_run_id,))
        canonical_works = cur.fetchone()[0]
        cur.execute(
            f"SELECT count(*) FROM work w {_latest_quality_lateral('w', quality_generation_id)} "
            "WHERE w.run_id = %s AND q.decision = 'include' "
            "AND w.effective_date >= %s AND w.effective_date < %s",
            (normalize_run_id, period_from, as_of),
        )
        eligible_works_in_period = cur.fetchone()[0]
        cur.execute(
            f"""SELECT q.decision, count(*) FROM work w {_latest_quality_lateral('w', quality_generation_id)}
            WHERE w.run_id = %s GROUP BY q.decision""", (normalize_run_id,),
        )
        quality_counts = {decision: count for decision, count in cur.fetchall()}

    checks = {
        'quality_generation_pinned': {'passed': quality_generation_id is not None,
                                      'observed': quality_generation_id, 'required': 'закреплённое поколение'},
        "peer_topic_coverage": {
            "passed": topic_count >= int(acceptance["min_peer_topics"]),
            "observed": topic_count, "required": acceptance["min_peer_topics"],
        },
        "temporal_leakage": {
            "passed": temporal_leaks == 0, "observed": temporal_leaks,
            "required": 0,
        },
        "quality_filter_enforced": {
            "passed": noninclude_memberships == 0,
            "observed": noninclude_memberships, "required": 0,
        },
        "no_one_window_forming": {
            "passed": forming_single_window <= int(acceptance["max_forming_single_window_topics"]),
            "observed": forming_single_window,
            "required_max": acceptance["max_forming_single_window_topics"],
        },
        "maturity_gate_enforced": {
            "passed": forming_too_mature <= int(acceptance["max_forming_above_maturity_percentile"]),
            "observed": forming_too_mature,
            "required_max": acceptance["max_forming_above_maturity_percentile"],
        },
        "repeatable_partition": {
            "passed": repeatable,
            "current_run_id": cluster_run_id, "previous_run_id": previous_run_id,
            "current_signature": current_signature,
            "previous_signature": previous_signature,
        },
    }
    infrastructure_passed = all(
        check["passed"] is True for check in checks.values()
    )
    target_label = benchmark.get("target_label") or benchmark["name"]
    interpretation = interpret_target_result(target_rows, recovery, target_label)
    conclusion = interpretation["conclusion"]
    if recovery["detected_as_distinct_topic"]:
        conclusion += (
            f" Статус темы: {dominant_status or 'неизвестен'}. "
            + ("Правила слабого сигнала пройдены."
               if qualified_signal else "Правила слабого сигнала не пройдены.")
        )
    return {
        "mission_id": mission_id,
        "benchmark_name": benchmark["name"],
        "as_of_date": as_of.isoformat(),
        'period_origin': period_origin,
        'evaluation_version': '0.4-topic-and-signal-separated',
        'evaluation_protocol_hash': hashlib.sha256(json.dumps(benchmark, sort_keys=True).encode()).hexdigest(),
        'limitations': ['Восстановление контрольной темы не равно выявлению слабого сигнала или проверке точности.',
                        'Development-ретротест; не закрытая внешняя оценка.',
                        'Старый прогон без quality_generation использует legacy-решения и не считается воспроизводимым.'],
        "blind_input_statement": benchmark.get("blind_input_statement", (
            "Контрольные идентификаторы применены только при постфактум-оценке и не "
            "участвовали в сборе, эмбеддингах, кластеризации или скоринге."
        )),
        "runs": {"normalize": normalize_run_id, "cluster": cluster_run_id,
                 "score": score_run_id, "embedding_model": model,
                 "clustering_backend": current_backend, "scale": scale,
                 'quality_generation_id': quality_generation_id},
        "corpus": {"raw_records": raw_counts, "raw_collection_batch_id": collection_batch_id,
                   'raw_count_provenance': 'pinned_batch' if collection_batch_id else 'unknown_legacy_batch',
                   "canonical_works": canonical_works, 'eligible_works_in_period': eligible_works_in_period,
                   "quality_decisions": quality_counts},
        "discovery": {"topic_lines": topic_count, "candidate_statuses": statuses,
                      "windows": windows},
        "target_recovery": recovery,
        "target_collection_coverage": interpretation["collection_coverage"],
        "target_failure_stage": interpretation["failure_stage"],
        "target_publications": [
            target_rows[f"{item['kind']}:{item['value']}"] for item in targets
        ],
        "controls": checks,
        "pipeline_controls_passed": infrastructure_passed,
        'target_topic_recovery_passed': recovery['detected_as_distinct_topic'],
        'target_candidate_status': dominant_status,
        "target_detection_passed": bool(qualified_signal),
        "overall_benchmark_passed": bool(
            infrastructure_passed and qualified_signal
        ),
        "conclusion": conclusion,
        "next_step": interpretation["next_step"],
    }


def benchmark_markdown(report: dict[str, Any]) -> str:
    verdict = "ПРОЙДЕН" if report["overall_benchmark_passed"] else "НЕ ПРОЙДЕН"
    recovery = report["target_recovery"]
    collection_coverage = report["target_collection_coverage"]
    controls = report["controls"]
    lines = [
        f"# Ретроспективный тест Horizon: {report['benchmark_name']}",
        "",
        f"**Итог полного теста: {verdict}.** {report['conclusion']}",
        "",
        "Восстановление контрольной темы и прохождение правил слабого сигнала проверяются "
        "раздельно. Полная инженерная проверка "
        f"{'пройдена' if report['pipeline_controls_passed'] else 'ещё не подтверждена'}; "
        "неизвестный результат контроля не считается ни успехом, ни доказанным сбоем.",
        "",
        "## Условия слепого теста",
        "",
        f"- Дата ретроспективного анализа публикаций: **{report['as_of_date']}**; публикации этой даты и позже исключены. Модели современные.",
        f"- {report['blind_input_statement']}",
        f"- Эмбеддинги: `{report['runs']['embedding_model']}`; кластеризация: `{report['runs']['clustering_backend']}`.",
        f"- Найдено тематических линий: **{report['discovery']['topic_lines']}**.",
        "",
        "## Проверка целевого сигнала",
        "",
        f"- Контрольных публикаций в наборе: **{collection_coverage['control_publications']}**.",
        f"- Найдены поисковым контуром в корпусе: **{collection_coverage['found_in_corpus']}**; отсутствуют: **{collection_coverage['missing_from_corpus']}**.",
        f"- Найдены и допущены к анализу на срезе: **{recovery['eligible_target_works']}**.",
        f"- Попали хотя бы в один кластер: **{recovery['assigned_target_works']}**.",
        f"- Максимум контрольных работ в одной теме: **{recovery['max_target_works_in_one_topic']}**.",
        f"- Доля контрольных работ в наиболее близкой теме: **{recovery['dominant_target_share'] if recovery['dominant_target_share'] is not None else 'н/д'}**.",
        f"- Целевой сигнал выделен самостоятельной темой: **{'да' if recovery['detected_as_distinct_topic'] else 'нет'}**.",
        f"- Статус темы: **{report.get('target_candidate_status') or 'неизвестен'}**; правила слабого сигнала: **{'пройдены' if report['target_detection_passed'] else 'не пройдены'}**.",
        "",
        "| Идентификатор | Публикация | Решение качества | Кластер |",
        "|---|---|---|---|",
    ]
    for item in report["target_publications"]:
        title = item.get("title", "не найдена в корпусе").replace("|", "\\|")
        topic = item.get("topic_label") or ("шум" if item.get("eligible_for_clustering") else "не допущена")
        identifier = f"{item.get('identifier_kind')}: {item.get('identifier_value')}"
        lines.append(
            f"| [{identifier}]({item['source_url']}) | {title} | "
            f"{item.get('quality_decision', 'нет записи')} | {topic} |"
        )
    lines.extend(["", "## Контроли корректности", "",
                  "| Проверка | Результат | Наблюдение |", "|---|---:|---:|"])
    labels = {
        'quality_generation_pinned': 'Поколение качества закреплено',
        "peer_topic_coverage": "Не менее 20 соседних тем",
        "temporal_leakage": "Нет публикаций из будущего",
        "quality_filter_enforced": "Карантин не попал в темы",
        "no_one_window_forming": "Один всплеск не становится forming",
        "maturity_gate_enforced": "Зрелая тема не маскируется под слабый сигнал",
        "repeatable_partition": "Повторный прогон даёт то же разбиение",
    }
    for key, check in controls.items():
        passed = check.get('passed')
        state = 'пройдено' if passed is True else ('не пройдено' if passed is False else 'не проверено')
        observed = check.get("observed")
        if observed is None:
            observed = ('нет сопоставимого повторного прогона' if key == 'repeatable_partition' and passed is None
                        else ('совпало' if passed is True else ('не совпало' if passed is False else 'неизвестно')))
        lines.append(
            f"| {labels.get(key, key)} | {state} | {observed} |"
        )
    lines.extend([
        "", "## Что это говорит о прототипе", "",
        "Это development-проверка, а не закрытая оценка precision/recall. Этап первого "
        f"установленного сбоя: **{report.get('target_failure_stage') or 'не установлен'}**. "
        "Результат охвата корпуса нельзя приписывать кластеризации. Современные модели и "
        "неполное восстановление исторических редакций также ограничивают выводы.",
        "", "## Следующая проверяемая доработка", "",
        report["next_step"], "",
        "Критерий приёмки следующей версии: сначала должен быть явно пройден контроль "
        "охвата, затем не менее двух допустимых контрольных работ должны соединиться в "
        "одну методическую линию; все нынешние контроли утечки и ложных сигналов обязаны "
        "остаться зелёными.", "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Слепой ретротест Horizon")
    parser.add_argument("mission_id")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--markdown", dest="markdown_path")
    parser.add_argument('--score-run', type=int, default=None)
    args = parser.parse_args()
    report = build_benchmark(args.mission_id, args.score_run)
    if args.json_path:
        path = Path(args.json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON: {path}")
    if args.markdown_path:
        path = Path(args.markdown_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(benchmark_markdown(report), encoding="utf-8")
        print(f"Markdown: {path}")
    if not args.json_path and not args.markdown_path:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
