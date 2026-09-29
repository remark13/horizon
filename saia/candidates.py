"""Расчёт признаков и evidence packet для тематических линий."""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from psycopg.types.json import Jsonb

from saia import db, methodology, runs
from saia.cluster import cosine_matrix, window_bounds, window_of, contiguous_windows
from saia.paper_evidence import assess_title, assess_title_v1, assess_works, choose_evidence
from saia.scoring import CandidateMetrics, assess_candidate
from saia.measurement import WindowCounts, analyze_series
from saia.research_identity import team_proxy


MIN_PERCENTILE_PEERS = 5
FEATURE_CODE = "candidates-v9-title-context-diagnostics-and-coverage-tristate"

_GENERIC_LABEL_TERMS = frozenset({
    "data", "design", "engineering", "framework", "frameworks", "model",
    "models", "properties", "property", "regime", "single", "system",
    "systems", "study", "method", "methods",
})
_GENERIC_CONTEXT_DETAILS = frozenset({"cell", "tissue"})
_RU_LABEL_FILLERS = frozenset({
    "в", "во", "на", "по", "для", "из", "при", "и", "или",
    "данные", "данных", "работа", "работы", "исследование", "исследования",
})


def composition_sha256(work_ids: list[int]) -> str:
    """Stable identity of an exact publication composition across score runs."""
    if not work_ids or len(work_ids) != len(set(work_ids)):
        raise ValueError("Состав темы должен содержать разные work_id.")
    return hashlib.sha256(
        json.dumps(sorted(work_ids), separators=(",", ":")).encode()
    ).hexdigest()


def primary_source_observation(titles: list[str], review_patterns: list[str]) -> dict:
    """Return a title proxy while keeping verified primary evidence unknown.

    The absence of words such as ``survey`` or ``review`` is not a content
    label.  The proxy can help populate an annotation queue, but must never be
    passed to the G6 primary-source gate as a verified count.
    """
    non_review_titles = sum(
        not any(re.search(r'\b' + re.escape(pattern) + r'\b', title.casefold())
                for pattern in review_patterns)
        for title in titles
    )
    return {
        "verified_primary_sources": None,
        "non_review_title_candidates": non_review_titles,
        "proxy_is_scientific_evidence": False,
    }


def confirmation_independence(dual_source_docs: int, total_docs: int,
                              arxiv_independent: bool,
                              openalex_independent: bool) -> float:
    """Оценить независимость подтверждения, не путая зеркало с каналом.

    Совпадение arXiv и OpenAlex повышает качество метаданных, но если один
    источник был вызван по ID другого, это не два независимых обнаружения.
    """
    if arxiv_independent and openalex_independent:
        return min(1.0, dual_source_docs / max(total_docs, 1))
    return min(0.45, 0.20 + 0.05 * dual_source_docs)


def percentile(value: float | None, population: list[float],
               min_peers: int = MIN_PERCENTILE_PEERS,
               rank_method: str = 'midrank') -> float | None:
    # Процентиль по двум темам создаёт ложную точность: одна неизбежно
    # получает 100 и может пройти шлюз независимо от абсолютного качества.
    if value is None or len(population) < min_peers:
        return None
    if rank_method != 'midrank':
        raise ValueError('Поддерживается только midrank для равных значений.')
    return round(100.0 * (sum(item < value for item in population)
                         + 0.5 * sum(item == value for item in population)) / len(population), 2)


def prevalence_ranks(shares: dict[int, float | None], min_peers: int,
                     rank_method: str, peer_scope: str) -> tuple[dict[int, float | None], int]:
    if peer_scope != 'active_in_last_full_window':
        raise ValueError('Поддерживаются только активные темы последнего полного окна для распространённости.')
    # Исторические затухшие линии не должны искусственно поднимать ранг
    # редкой текущей темы. Неактивная линия сохраняет нулевую долю, но не
    # увеличивает число доступных текущих соседей.
    population = [s for s in shares.values() if s is not None and s > 0]
    return ({key: percentile(value, population, min_peers, rank_method)
             for key, value in shares.items()}, len(population))


def parse_vector(value: str | None) -> np.ndarray | None:
    if not value:
        return None
    return np.fromstring(value.strip("[]"), sep=",", dtype=np.float32)


def mean_pairwise(vectors: list[np.ndarray]) -> float | None:
    if len(vectors) < 2:
        return None
    matrix = cosine_matrix(np.vstack(vectors), np.vstack(vectors))
    upper = matrix[np.triu_indices(len(vectors), 1)]
    return float(upper.mean()) if len(upper) else None


def source_links(identifiers: list[tuple[str, str]]) -> list[dict]:
    links = []
    for kind, value in identifiers:
        if kind == "doi":
            links.append({"type": "doi", "url": f"https://doi.org/{value}"})
        elif kind == "arxiv":
            links.append({"type": "arxiv", "url": f"https://arxiv.org/abs/{value}"})
        elif kind == "openalex":
            links.append({"type": "openalex", "url": f"https://openalex.org/{value}"})
    unique = {(item["type"], item["url"]): item for item in links}
    return list(unique.values())


def choose_topic_label(rows: list[tuple[str, int]], query_terms: list[str],
                       cluster_label: str) -> tuple[str, str]:
    """Never rename a discovered subtopic back to the user's broad seed."""
    seeds = {" ".join(term.casefold().split()) for term in query_terms}
    specific = [row for row in rows if " ".join(row[0].casefold().split()) not in seeds]
    if specific:
        term, _ = max(specific, key=lambda row: (row[1], len(row[0].split()), len(row[0])))
        return term.capitalize(), "automatic_non_seed_term_from_latest_evidence"
    label = readable_cluster_label(cluster_label, query_terms)
    return label, (
        "generic_query_context_no_specific_cluster_terms"
        if label.startswith("Тема по запросу «") else
        "readable_cluster_terms_seed_context_only"
    )


def _term_key(value: str) -> str:
    value = " ".join(value.casefold().replace("_", " ").split())
    irregular = {"properties": "property", "studies": "study"}
    if value in irregular:
        return irregular[value]
    if " " not in value and len(value) > 4 and value.endswith("s"):
        return value[:-1]
    return value


def _natural_join(values: list[str]) -> str:
    conjunction = ("и" if all(re.search(r"[а-яё]", value, re.IGNORECASE)
                           and not re.search(r"[a-z]", value, re.IGNORECASE)
                           for value in values) else "and")
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} {conjunction} {values[1]}"
    return ", ".join(values[:-1]) + f" {conjunction} {values[-1]}"


def _russian_seed_only(terms: list[str], query_terms: list[str]) -> bool:
    """Detect labels consisting only of Russian query inflections and filler."""
    if not query_terms or not terms or not re.search(r"[а-яё]", query_terms[0], re.IGNORECASE):
        return False
    if any(" " in term or not re.fullmatch(r"[а-яё-]+", term, re.IGNORECASE)
           for term in terms):
        return False
    seed_words = [word for query in query_terms
                  for word in re.findall(r"[а-яё]+", query.casefold()) if len(word) >= 5]
    return all(
        term.casefold() in _RU_LABEL_FILLERS
        or any(len(term) >= 5 and SequenceMatcher(None, term.casefold(), seed).ratio() >= 0.85
               for seed in seed_words)
        for term in terms
    )


def readable_cluster_label(cluster_label: str, query_terms: list[str]) -> str:
    """Turn BERTopic slash terms into a compact, deterministic readable name.

    This is presentation only: it never changes membership or score and does
    not pretend to translate or interpret a topic with an LLM.
    """
    terms = [" ".join(term.split()) for term in (cluster_label or "").split("/")]
    has_confidence_interval = any(re.fullmatch(
        r"\d{1,3}\s*%?\s*ci", term, flags=re.IGNORECASE) for term in terms)
    terms = [term for term in terms if term and not re.fullmatch(
        r"\d{1,3}\s*%?\s*ci", term, flags=re.IGNORECASE)
        and not (has_confidence_interval and term.casefold() == "ci")]
    if any(term.casefold() in {"model", "models"} for term in terms):
        terms = ["large language models" if term.casefold() == "large language" else term
                 for term in terms]
    if not terms:
        return "Тематическая линия"
    if _russian_seed_only(terms, query_terms):
        return f"Тема по запросу «{query_terms[0]}»"
    seed_keys = {_term_key(term) for term in query_terms}
    context = next((term for term in terms if " " in term), None)
    context_tokens = set(_term_key(context).split()) if context else set()
    chosen: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = _term_key(term)
        if key in seen or key in _GENERIC_LABEL_TERMS:
            continue
        if key in seed_keys and term != context:
            continue
        if " " not in term and key in context_tokens:
            continue
        seen.add(key)
        chosen.append(term)
    if context and _term_key(context) in seed_keys:
        details = [
            term for term in chosen
            if term != context and _term_key(term) not in _GENERIC_CONTEXT_DETAILS
        ][:3]
        if details:
            return f"{context.capitalize()} — {_natural_join(details)}"
    if context and context in chosen and len(chosen) > 1:
        details = [
            term for term in chosen
            if term != context and _term_key(term) not in _GENERIC_CONTEXT_DETAILS
        ][:2]
        if not details:
            return context.capitalize()
        return f"{context.capitalize()} — {_natural_join(details)}"
    selected = chosen[:4] or terms[:3]
    return _natural_join(selected).capitalize()


def label_for_topic(cur, topic_id: int, latest_window: str, quality_generation_id: int,
                    query_terms: list[str]) -> tuple[str, str]:
    cur.execute(
        """
        SELECT unnest(q.matched_terms) AS term, count(*)
        FROM topic_membership tm JOIN quality_snapshot q USING (work_id)
        WHERE tm.topic_id = %s AND tm.window_key = %s AND q.generation_id = %s
        GROUP BY term
        """,
        (topic_id, latest_window, quality_generation_id),
    )
    rows = cur.fetchall()
    cur.execute("SELECT COALESCE(label, 'Тематическая линия') FROM topic WHERE topic_id = %s", (topic_id,))
    return choose_topic_label(rows, query_terms, cur.fetchone()[0])


def topic_rows(cur, cluster_run_id: int, model: str, quality_generation_id: int) -> dict[int, dict]:
    cur.execute(
        """
        SELECT t.topic_id, t.first_window, t.last_window, t.anchor_embedding::text,
               ts.window_key, ts.window_end, ts.doc_count, ts.popularity,
               ts.slope, ts.popularity_percentile
        FROM topic t JOIN topic_snapshot ts USING (topic_id)
        WHERE t.run_id = %s
        ORDER BY t.topic_id, ts.window_start
        """,
        (cluster_run_id,),
    )
    topics: dict[int, dict] = {}
    for topic_id, first, last, anchor, window, end, count, popularity, slope, pop_pct in cur.fetchall():
        topic = topics.setdefault(topic_id, {
            "topic_id": topic_id, "first_window": first, "last_window": last,
            "anchor": parse_vector(anchor), "snapshots": [], "works": [],
        })
        topic["snapshots"].append({
            "window": window, "end": end, "doc_count": count,
            "popularity": float(popularity), "slope": float(slope or 0.0),
            "popularity_percentile": float(pop_pct) if pop_pct is not None else None,
        })
    cur.execute(
        """
        SELECT tm.topic_id, tm.window_key, w.work_id, w.canonical_title,
               w.effective_date, w.abstract, w.counts_by_year,
               q.source_quality, q.matched_terms, e.embedding::text
        FROM topic_membership tm
        JOIN topic t ON t.topic_id = tm.topic_id
        JOIN work w ON w.work_id = tm.work_id
        JOIN quality_snapshot q ON q.work_id = w.work_id AND q.generation_id = %s
        LEFT JOIN work_embedding e ON e.work_id = w.work_id AND e.model = %s
        WHERE t.run_id = %s AND q.decision = 'include'
        ORDER BY tm.topic_id, w.effective_date, w.work_id
        """,
        (quality_generation_id, model, cluster_run_id),
    )
    for topic_id, window, work_id, title, effective, abstract, counts, quality, terms, vector in cur.fetchall():
        topics[topic_id]["works"].append({
            "window": window, "work_id": work_id, "title": title,
            "effective_date": effective, "abstract": abstract,
            "counts_by_year": counts or [], "source_quality": float(quality),
            "matched_terms": list(terms or []), "vector": parse_vector(vector),
        })
    return topics


def build_candidates(mission_id: str, cluster_run_id: int | None = None,
                     historical_novelty_report: Path | None = None,
                     historical_novelty_sensitivity: Path | None = None,
                     config: methodology.Methodology | None = None) -> dict:
    # Controlled before/after replay may use the validated passport embedded
    # in an earlier run. Ordinary production calls still use today's default.
    config = config or methodology.load_default()
    with db.connect() as conn, conn.cursor() as cur:
        if cluster_run_id is None:
            cluster_run_id = runs.current_run_id(cur, mission_id, "cluster")
        elif (not isinstance(cluster_run_id, int) or isinstance(cluster_run_id, bool)
              or cluster_run_id < 1):
            raise ValueError("Номер cluster-прогона должен быть положительным целым")
        if cluster_run_id is None:
            raise ValueError("сначала требуется завершённый прогон кластеризации")
        cur.execute(
            "SELECT embedding_model, window_step, upstream_run_id, clustering_scale, notes, query_version_id "
            "FROM analysis_run WHERE run_id = %s",
            (cluster_run_id,),
        )
        cluster_row = cur.fetchone()
        if not cluster_row:
            raise ValueError('Нужен точный завершённый cluster-прогон этой миссии.')
        model, step, normalize_run_id, scale, cluster_notes, query_version_id = cluster_row
        cur.execute(
            "SELECT 1 FROM analysis_run WHERE run_id=%s AND mission_id=%s "
            "AND kind='cluster' AND status='done'",
            (cluster_run_id, mission_id),
        )
        if not cur.fetchone():
            raise ValueError('Нужен точный завершённый cluster-прогон этой миссии.')
        cur.execute("SELECT terms FROM query_version WHERE query_version_id=%s", (query_version_id,))
        query_row = cur.fetchone()
        if not query_row:
            raise ValueError('Версия запроса кластеризации не найдена.')
        query_terms = list(query_row[0] or [])
        quality_generation_id = (cluster_notes or {}).get('quality_generation_id')
        if not quality_generation_id:
            raise ValueError('Старый кластер не закрепляет поколение качества. Выполните quality и cluster для v0.4; прежний результат сохранён.')
        cur.execute("SELECT as_of_date FROM mission WHERE mission_id = %s", (mission_id,))
        as_of: date = cur.fetchone()[0]
        period_start, observation_end, period_origin = runs.analysis_period(cur, normalize_run_id)
        topics = topic_rows(cur, cluster_run_id, model, quality_generation_id)
        measurement_cfg = config.raw['publication_measurement']
        min_peers = int(measurement_cfg['min_percentile_peers'])
        percentile_method = measurement_cfg['percentile_rank']
        cur.execute('SELECT notes FROM analysis_run WHERE run_id = %s', (normalize_run_id,))
        normalize_notes = cur.fetchone()[0] or {}
        batch_id = normalize_notes.get('collection_batch_id')
        cur.execute('SELECT coverage FROM collection_batch WHERE batch_id = %s', (batch_id,))
        coverage_row = cur.fetchone()
        coverage = coverage_row[0] if coverage_row else {}
        # Нельзя доказать сопоставимость только статусом complete. Для этого
        # нужен отдельный проверенный паспорт временного покрытия пакета.
        from saia.coverage_passport import resolve as resolve_coverage_passport
        comparable_coverage, coverage_passport_id = resolve_coverage_passport(
            cur, batch_id, coverage
        )
        cur.execute('SELECT w.effective_date FROM work w JOIN quality_snapshot q USING (work_id) '
                    "WHERE w.run_id = %s AND q.generation_id = %s AND q.decision = 'include' "
                    'AND w.effective_date < %s AND (%s::date IS NULL OR w.effective_date >= %s) '
                    'ORDER BY w.effective_date',
                    (normalize_run_id, quality_generation_id, observation_end, period_start, period_start))
        corpus_dates = [row[0] for row in cur.fetchall()]
        if not corpus_dates:
            raise ValueError('Нет допущенного аналитического корпуса.')
        corpus_counts = Counter(window_of(d, step) for d in corpus_dates)
        cur.execute('SELECT w.effective_date FROM work w JOIN quality_snapshot q USING (work_id) '
                    'JOIN work_embedding e ON e.work_id = w.work_id AND e.model = %s '
                    "WHERE w.run_id = %s AND q.generation_id = %s AND q.decision = 'include' "
                    'AND w.effective_date < %s AND (%s::date IS NULL OR w.effective_date >= %s)',
                    (model, normalize_run_id, quality_generation_id, observation_end, period_start, period_start))
        embedded_counts = Counter(window_of(r[0], step) for r in cur.fetchall())
        grid = contiguous_windows(window_of(min(corpus_dates), step),
                                  window_of(observation_end - timedelta(days=1), step), step)
        series_by_topic = {}
        for topic_id, topic in topics.items():
            counts = Counter(window_of(w['effective_date'], step) for w in topic['works'])
            intervals = []
            for key in grid:
                start, inclusive_end = window_bounds(key, step)
                calendar_end = inclusive_end + timedelta(days=1)
                intervals.append(WindowCounts(start, min(calendar_end, observation_end),
                                              counts[key], corpus_counts[key],
                                              complete=calendar_end <= observation_end,
                                              coverage_comparable=comparable_coverage))
            # Значения показываем даже при неизвестном покрытии; interpretation
            # оставит slope неизвестным, а допуск отправит тему на проверку.
            series_by_topic[topic_id] = analyze_series(intervals, as_of)

        full_keys = [key for key in grid if window_bounds(key, step)[1] < observation_end]
        from saia.measurement import POLICY_PATH as MEASUREMENT_POLICY_PATH
        import yaml
        history_windows = int(yaml.safe_load(MEASUREMENT_POLICY_PATH.read_text())['history_windows'])
        embedding_by_window = [
            {'window': key, 'corpus_works': corpus_counts[key], 'embedded_works': embedded_counts[key],
             'coverage': embedded_counts[key] / corpus_counts[key] if corpus_counts[key] else None}
            for key in full_keys[-history_windows:]
        ]
        embedding_coverage = (min(p['coverage'] for p in embedding_by_window)
                              if len(embedding_by_window) == history_windows
                              and all(p['coverage'] is not None for p in embedding_by_window) else None)

        # Новизна темы измеряется только относительно якорей, существовавших
        # до её первого окна. Для самой ранней линии значение неизвестно.
        novelty_raw: dict[int, float | None] = {}
        for topic_id, topic in topics.items():
            previous = [
                other["anchor"] for other in topics.values()
                if other["first_window"] < topic["first_window"] and other["anchor"] is not None
            ]
            novelty_raw[topic_id] = (
                1.0 - float(cosine_matrix(topic["anchor"][None, :], np.vstack(previous)).max())
                if previous and topic["anchor"] is not None else None
            )
        novelty_population = [value for value in novelty_raw.values() if value is not None]
        if bool(historical_novelty_report) != bool(historical_novelty_sensitivity):
            raise ValueError(
                "Historical novelty requires both a diagnostic and sensitivity report"
            )
        historical_novelty_values = None
        historical_novelty_provenance = None
        if historical_novelty_report and historical_novelty_sensitivity:
            from saia.historical_novelty_sensitivity import validated_score_values
            historical_novelty_values, historical_novelty_provenance = validated_score_values(
                Path(historical_novelty_report), Path(historical_novelty_sensitivity),
                cluster_run_id=cluster_run_id, model=model,
                topic_ids=set(topics),
            )
            novelty_raw = {
                topic_id: historical_novelty_values[topic_id]["novelty_raw"]
                for topic_id in topics
            }
            novelty_population = list(novelty_raw.values())

        latest_slopes = [s['share_slope_per_window'] for s in series_by_topic.values()
                         if s['share_slope_per_window'] is not None]
        prevalence = {k: next((p['share'] for p in reversed(s['points']) if p['complete']), None)
                      for k, s in series_by_topic.items()}
        prevalence_percentiles, prevalence_peer_count = prevalence_ranks(
            prevalence, min_peers, percentile_method, measurement_cfg['prevalence_peer_scope'])
        citation_raw = {}
        for topic_id, topic in topics.items():
            citations = 0
            exposure = 0.0
            measured = False
            for work in topic["works"]:
                if work["counts_by_year"]:
                    measured = True
                    citations += sum(
                        int(item.get("cited_by_count") or 0)
                        for item in work["counts_by_year"]
                        if int(item.get("year") or 9999) < as_of.year
                    )
                    exposure += max((as_of - work["effective_date"]).days / 365.25, 0.25)
            citation_raw[topic_id] = (
                citations / exposure if measured and exposure else None
            )
        citation_population = [value for value in citation_raw.values() if value is not None]

        score_run_id = runs.start_run(
            cur, mission_id, "score", config, embedding_model=model,
            upstream_run_id=cluster_run_id, window_step=step,
            clustering_scale=scale,
            notes={"candidate_topics": len(topics), "feature_code": FEATURE_CODE,
                   'effective_config': config.raw,
                   'quality_generation_id': quality_generation_id,
                   'collection_batch_id': batch_id,
                   'coverage_passport_id': coverage_passport_id,
                   'historical_mode': measurement_cfg['historical_mode'],
                   'historical_novelty': historical_novelty_provenance,
                   'measurement_version': next(iter(series_by_topic.values()))['measurement_version'] if series_by_topic else None},
        )
        summary = Counter()

        # Авторские множества нужны и для независимости команд, и для bridge.
        authors_by_topic: dict[int, set[int]] = {}
        for topic_id in topics:
            cur.execute(
                """
                SELECT DISTINCT wa.author_id FROM topic_membership tm
                JOIN work_author wa USING (work_id)
                WHERE tm.topic_id = %s
                """,
                (topic_id,),
            )
            authors_by_topic[topic_id] = {row[0] for row in cur.fetchall()}

        for topic_id, topic in topics.items():
            works = topic["works"]
            if not works:
                continue
            latest = topic["snapshots"][-1]
            present_windows = [snap for snap in topic["snapshots"] if snap["doc_count"] > 0]

            cur.execute(
                """
                SELECT count(DISTINCT wa.organisation_id),
                       count(DISTINCT a.name_key) FILTER (WHERE wa.author_position = 0),
                       count(wa.organisation_id)
                FROM topic_membership tm JOIN work_author wa USING (work_id)
                JOIN author a USING (author_id)
                WHERE tm.topic_id = %s
                """,
                (topic_id,),
            )
            independent_orgs, _, organisation_links = cur.fetchone()
            if organisation_links == 0:
                independent_orgs = None
            cur.execute('SELECT w.work_id, array_agg(DISTINCT a.external_id) '
                        'FILTER (WHERE a.external_id IS NOT NULL), '
                        'array_agg(DISTINCT wa.organisation_id) FILTER (WHERE wa.organisation_id IS NOT NULL), '
                        "bool_or(COALESCE((q.flags->>'author_identity_conflict')::boolean, false)) "
                        'FROM work w LEFT JOIN work_author wa USING (work_id) '
                        'LEFT JOIN author a USING (author_id) '
                        'JOIN quality_snapshot q ON q.work_id = w.work_id AND q.generation_id = %s '
                        'WHERE w.work_id = ANY(%s) GROUP BY w.work_id',
                        (quality_generation_id, [w['work_id'] for w in works]))
            identity = team_proxy([{'authors': ids or [], 'organisations': orgs or [], 'identity_conflict': conflict}
                                   for _, ids, orgs, conflict in cur.fetchall()],
                                  float(measurement_cfg['team_overlap_jaccard_min']),
                                  float(measurement_cfg['min_author_identity_coverage']))
            independent_teams = identity['teams']
            cur.execute(
                """
                SELECT max(n)::float / nullif(
                    (SELECT count(DISTINCT tm2.work_id)
                     FROM topic_membership tm2 JOIN work_author wa2 USING (work_id)
                     WHERE tm2.topic_id = %s AND wa2.organisation_id IS NOT NULL), 0
                ) FROM (
                    SELECT wa.organisation_id, count(DISTINCT tm.work_id) n
                    FROM topic_membership tm JOIN work_author wa USING (work_id)
                    WHERE tm.topic_id = %s AND wa.organisation_id IS NOT NULL
                    GROUP BY wa.organisation_id
                ) shares
                """,
                (topic_id, topic_id),
            )
            single_org_share = cur.fetchone()[0]
            vectors = [work["vector"] for work in works if work["vector"] is not None]
            coherence = mean_pairwise(vectors)

            other_authors = set().union(*(
                value for key, value in authors_by_topic.items() if key != topic_id
            )) if len(authors_by_topic) > 1 else set()
            topic_authors = authors_by_topic[topic_id]
            bridge = (
                len(topic_authors & other_authors) / len(topic_authors)
                if topic_authors else None
            )

            series = series_by_topic[topic_id]
            historical_topic_novelty = (
                historical_novelty_values[topic_id]
                if historical_novelty_values is not None else None
            )
            novelty_pct = (
                historical_topic_novelty["novelty_percentile"]
                if historical_topic_novelty is not None else
                percentile(novelty_raw[topic_id], novelty_population,
                           min_peers, percentile_method)
            )
            momentum_pct = percentile(series['share_slope_per_window'], latest_slopes, min_peers, percentile_method)
            citation_pct = percentile(citation_raw[topic_id], citation_population)
            novelty_availability = (
                historical_topic_novelty["novelty_availability"]
                if historical_topic_novelty is not None else
                "available" if novelty_pct is not None else
                "no_prior_topic_anchor" if novelty_raw[topic_id] is None else
                "insufficient_peer_topics"
            )
            momentum_availability = (
                "available" if momentum_pct is not None else
                "insufficient_full_windows" if series['used_full_windows'] < series['required_history_windows'] else
                "coverage_unknown" if series['coverage_comparable'] is None else
                "coverage_not_comparable" if series['coverage_comparable'] is False else
                "insufficient_peer_topics" if len(latest_slopes) < min_peers else
                "unavailable"
            )
            first_found = min(work["effective_date"] for work in works)
            age_years = (as_of - first_found).days / 365.25

            abstract_ratio = sum(bool(work["abstract"]) for work in works) / len(works)
            quality_mean = sum(work["source_quality"] for work in works) / len(works)
            modes = coverage.get("source_modes", {})
            arxiv_independent = modes.get("arxiv", {}).get("independent_discovery", False)
            openalex_independent = modes.get("openalex", {}).get(
                "independent_discovery", False
            )
            dual_source_docs = 0
            for work in works:
                cur.execute("SELECT count(DISTINCT source) FROM work_version WHERE work_id = %s", (work["work_id"],))
                dual_source_docs += int(cur.fetchone()[0] >= 2)
            confirmation = confirmation_independence(
                dual_source_docs, len(works), arxiv_independent,
                openalex_independent,
            )
            affiliation_completeness = identity['affiliation_coverage']
            if affiliation_completeness < float(measurement_cfg['min_affiliation_coverage']):
                independent_orgs = None
            data_completeness = 0.70 * abstract_ratio + 0.30 * affiliation_completeness
            diffusion = None if independent_teams is None else min(
                1.0, independent_teams / int(measurement_cfg['independent_team_full_credit'])) * (
                1.0 - single_org_share if single_org_share is not None else 0.5
            )
            persistence = (None if series['consecutive_active_full_windows'] is None else
                           min(1.0, series['consecutive_active_full_windows'] / int(
                               measurement_cfg['persistence_full_credit_windows'])))
            # Эта оценка характеризует независимость исследовательских групп,
            # а не совпадение одной работы в двух библиографических источниках.
            research_confirmation = (diffusion or 0.0) * identity['identity_coverage']
            primary_observation = primary_source_observation(
                [work['title'] for work in works],
                measurement_cfg['primary_review_patterns'],
            )

            normalized = {
                "novelty": None if novelty_pct is None else novelty_pct / 100,
                "momentum": None if momentum_pct is None else momentum_pct / 100,
                "persistence": persistence,
                "independent_diffusion": diffusion,
                "author_topic_overlap": bridge,
                "citation_velocity": None if citation_pct is None else citation_pct / 100,
                "coherence": coherence,
            }
            penalties = {}
            if abstract_ratio < float(measurement_cfg['missing_abstract_threshold']):
                penalties["missing_abstract"] = config.penalties["missing_abstract"]
            metrics = CandidateMetrics(
                doc_count=len(works), independent_orgs=independent_orgs,
                independent_teams=independent_teams,
                single_org_share=single_org_share,
                maturity_percentile=(
                    prevalence_percentiles[topic_id]
                ),
                novelty_percentile=novelty_pct,
                windows_present=series['consecutive_active_full_windows'],
                momentum_percentile=momentum_pct,
                primary_sources=primary_observation['verified_primary_sources'], age_years=age_years,
                share_slope=series['share_slope_per_window'], share_change=series['share_change'],
                consecutive_active_windows=series['consecutive_active_full_windows'],
                coverage_comparable=comparable_coverage,
                coherence_calibrated=True if model in measurement_cfg['coherence_calibrated_models'] else None,
                embedding_coverage=embedding_coverage,
                normalized=normalized, penalties=penalties,
                confidence_components={
                    "data_completeness": data_completeness,
                    "source_quality": quality_mean,
                    "confirmation_independence": research_confirmation,
                },
            )
            assessment = assess_candidate(metrics, config)
            label, label_basis = label_for_topic(
                cur, topic_id, latest["window"], quality_generation_id, query_terms
            )
            paper_diagnostics, paper_diagnostic_summary = assess_works(works, label)
            # Исторические независимость и связность на дату раннего окна
            # пока не восстановлены. Один накопленный объём не доказывает birth.
            research_birth = None
            bounded_discovery = any(
                item.get('access_mode') == 'frozen-bounded-discovery-projection'
                for item in modes.values()
            )
            if bounded_discovery:
                limitations = [
                    'Тема построена из ограниченной поисковой выдачи OpenAlex/arXiv, не из полного корпуса области.',
                    'Попадание одной работы в оба источника не является независимым подтверждением исследования.',
                    'Динамика доли публикаций в этой выборке несопоставима по времени; рост не подтверждён.',
                    'Публикационные данные не подтверждают рынок или коммерческое внедрение.',
                ]
                if any(str(item.get('retrieval_origin') or '').startswith('previously_ingested_local_cache')
                       for item in modes.values()):
                    limitations.append(
                        'Часть публикаций OpenAlex взята из ранее загруженного локального кэша; '
                        'его полнота и актуальность не подтверждены.'
                    )
            elif not modes:
                limitations = [
                    'Паспорт способа сбора не закреплён за этим старым корпусом; независимость источников обнаружения не установлена.',
                    'Публикационные данные не подтверждают рынок или коммерческое внедрение.',
                ]
            elif arxiv_independent:
                limitations = [
                    "Слепой поиск выполнен внутри заранее заданных категорий arXiv, без названия проверяемого тренда.",
                    "OpenAlex используется для обогащения уже найденного arXiv-корпуса и не считается независимым каналом обнаружения.",
                    "Hashing-ngram и graph clustering — воспроизводимый CPU-baseline; требуется сравнение со SPECTER2/BERTopic.",
                    "Публикационные данные не подтверждают рынок или коммерческое внедрение.",
                ]
            else:
                limitations = [
                    "Направленный ретротест по словарю известной линии, не слепой поиск по полному ИИ-корпусу.",
                    "arXiv получен по ID из OpenAlex: это подтверждение версии, но не независимое обнаружение.",
                    "Hashing-ngram и graph clustering — воспроизводимый CPU-baseline; требуется сравнение со SPECTER2/BERTopic.",
                    "Публикационные данные не подтверждают рынок или коммерческое внедрение.",
                ]
            limitations = [s for s in limitations if not s.startswith('Hashing-ngram')]
            limitations.extend([
                f"Расчёт использует модель {model}, кластеризацию {scale}; это реконструкция современными инструментами.",
                ('Знаменатель — ограниченная несопоставимая выборка данной задачи.'
                 if bounded_discovery else
                 'Знаменатель — допущенный корпус данной миссии, не все публикации мира.'),
                'Независимость групп — консервативная оценка по идентификаторам авторов и организациям, не установленная независимость лабораторий.',
                'Первичные результаты определены предварительно по отсутствию обзорных маркеров; воспроизведение и преимущество не установлены.',
                'Дата первого прохождения правил не восстановлена; signal_detected пока не заполняется.',
            ])
            temporal_definition_scope = (cluster_notes or {}).get(
                'temporal_definition_scope'
            )
            if temporal_definition_scope == 'full_period_current_reconstruction_not_backtest':
                limitations.append(
                    'Состав темы определён по всему текущему периоду, а затем разложен '
                    'по кварталам. Ряд показывает текущую реконструкцию динамики, но не '
                    'доказывает обнаружение этой темы алгоритмом на историческую дату.'
                )
            if historical_topic_novelty is not None:
                limitations.append(
                    'Новизна сопоставлена с релевантными публикациями до начала '
                    'текущего периода. Перцентиль используется в score только когда '
                    'он неизменен во всех проверенных вариантах параметров фоновой кластеризации.'
                )
            detected = None
            cur.execute(
                """
                INSERT INTO signal_candidate
                    (run_id, topic_id, status, label, label_basis, first_found,
                     research_birth, signal_detected, emergence_score,
                     evidence_confidence, metrics, gates, limitations)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING candidate_id
                """,
                (
                    score_run_id, topic_id, assessment.status, label, label_basis,
                    first_found, research_birth, detected, assessment.emergence_score,
                    assessment.evidence_confidence, Jsonb({
                        "observed": {
                            "doc_count": len(works), "independent_orgs": independent_orgs,
                            "independent_teams": independent_teams,
                            "single_org_share": single_org_share,
                            "maturity_percentile": (
                                prevalence_percentiles[topic_id]
                            ),
                            "peer_topics": len(topics),
                            'prevalence_peer_topics': prevalence_peer_count,
                            'prevalence_peer_scope': measurement_cfg['prevalence_peer_scope'],
                            'publication_share': prevalence[topic_id],
                            'prevalence_semantics': 'Доля новых работ в полном окне внутри корпуса миссии; не техническая или рыночная зрелость.',
                            "min_percentile_peers": min_peers,
                            "novelty_raw": novelty_raw[topic_id],
                            "novelty_percentile": novelty_pct,
                            "novelty_availability": novelty_availability,
                            **({
                                "novelty_basis": "nearest_pre_period_semantic_topic_anchor",
                                "nearest_background_topic": historical_topic_novelty[
                                    "nearest_background_topic"
                                ],
                                "nearest_background_similarity": historical_topic_novelty[
                                    "nearest_background_similarity"
                                ],
                                "nearest_background_terms": historical_topic_novelty[
                                    "nearest_background_terms"
                                ],
                                "historical_anchor_topics": historical_topic_novelty[
                                    "historical_anchor_topics"
                                ],
                                "novelty_sensitivity_percentile_min": historical_topic_novelty[
                                    "sensitivity_percentile_min"
                                ],
                                "novelty_sensitivity_percentile_max": historical_topic_novelty[
                                    "sensitivity_percentile_max"
                                ],
                                "novelty_sensitivity_variants": historical_topic_novelty[
                                    "sensitivity_variants"
                                ],
                            } if historical_topic_novelty is not None else {}),
                            "windows_present": series['consecutive_active_full_windows'],
                            'observed_consecutive_active_windows': series['observed_consecutive_active_full_windows'],
                            'total_active_windows': len(present_windows),
                            "momentum_raw": series['share_slope_per_window'],
                            "momentum_percentile": momentum_pct,
                            "momentum_availability": momentum_availability,
                            "momentum_used_full_windows": series['used_full_windows'],
                            "momentum_required_full_windows": series['required_history_windows'],
                            "citation_velocity_raw": citation_raw[topic_id],
                            "coherence": coherence, "author_topic_overlap": bridge,
                            'primary_sources_verified': primary_observation['verified_primary_sources'],
                            'non_review_title_candidates': primary_observation['non_review_title_candidates'],
                            'title_proxy_is_scientific_evidence': primary_observation['proxy_is_scientific_evidence'],
                            'paper_title_diagnostics': paper_diagnostic_summary,
                            'identity_coverage': identity['identity_coverage'],
                            'affiliation_coverage': affiliation_completeness,
                            'metadata_source_overlap': dual_source_docs / len(works),
                            'metadata_confirmation_legacy_proxy': confirmation if modes else None,
                            'assessed_at': as_of.isoformat(),
                            'corpus_period_from': period_start.isoformat() if period_start else None,
                            'corpus_period_end_exclusive': observation_end.isoformat(),
                            'corpus_period_origin': period_origin,
                            'temporal_definition_scope': temporal_definition_scope,
                            'embedding_coverage': embedding_coverage,
                            'embedding_coverage_by_window': embedding_by_window,
                            "abstract_completeness": abstract_ratio,
                        },
                        "normalized": normalized,
                        "missing_metrics": list(assessment.missing_metrics),
                        "penalties": penalties,
                        'publication_series': series,
                    }), Jsonb([gate.__dict__ for gate in assessment.gates]), limitations,
                ),
            )
            candidate_id = cur.fetchone()[0]

            ranked = choose_evidence(
                works, paper_diagnostics, latest["window"],
                int(measurement_cfg['evidence_max_items']),
            )
            for rank, (work, role, paper_note) in enumerate(ranked, 1):
                cur.execute(
                    "SELECT kind, value FROM identifier WHERE work_id = %s",
                    (work["work_id"],),
                )
                links = source_links(cur.fetchall())
                cur.execute(
                    """
                    INSERT INTO evidence_item
                        (candidate_id, work_id, evidence_rank, role, rationale, sources)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        candidate_id, work["work_id"], rank, role,
                        paper_note + " Фрагмент аннотации или названия: "
                        + (work['abstract'] or work['title'])[:600],
                        Jsonb(links),
                    ),
                )
            summary[assessment.status] += 1

        runs.finish_run(cur, score_run_id, "done")
        conn.commit()
    return {"run_id": score_run_id, "cluster_run_id": cluster_run_id, **dict(summary)}


def export_cards(mission_id: str, score_run_id: int | None = None) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        score_run_id = score_run_id or runs.current_run_id(cur, mission_id, "score")
        if score_run_id is None:
            raise ValueError("нет завершённого score-прогона")
        cur.execute("SELECT 1 FROM analysis_run WHERE run_id = %s AND mission_id = %s "
                    "AND kind = 'score' AND status = 'done'", (score_run_id, mission_id))
        if not cur.fetchone():
            raise ValueError('Score-прогон отсутствует, не завершён или принадлежит другой миссии.')
        cur.execute(
            """WITH RECURSIVE chain AS (
                SELECT r.*, 0 AS depth FROM analysis_run r WHERE run_id = %s
                UNION ALL
                SELECT r.*, c.depth + 1 FROM analysis_run r
                JOIN chain c ON r.run_id = c.upstream_run_id
            ) SELECT run_id, kind, query_version_id, as_of_date,
                methodology_version, methodology_hash, code_version,
                embedding_model, window_step, clustering_scale,
                upstream_run_id, notes FROM chain ORDER BY depth""",
            (score_run_id,),
        )
        provenance = []
        for item in cur.fetchall():
            (run_id, kind, query_id, as_of, version, digest, code, model,
             step, scale, upstream, notes) = item
            provenance.append({
                'run_id': run_id, 'kind': kind, 'query_version_id': query_id,
                'as_of_date': as_of.isoformat(), 'methodology_version': version,
                'methodology_hash': digest, 'code_version': code,
                'embedding_model': model, 'window_step': step,
                'clustering_scale': scale, 'upstream_run_id': upstream,
                'notes': notes or {},
            })
        cur.execute(
            """
            SELECT candidate_id, topic_id, status, label, label_basis, first_found,
                   research_birth, signal_detected, emergence_score,
                   evidence_confidence, metrics, gates, limitations
            FROM signal_candidate WHERE run_id = %s
            ORDER BY emergence_score DESC NULLS LAST, evidence_confidence DESC
            """,
            (score_run_id,),
        )
        cards = []
        for row in cur.fetchall():
            (candidate_id, topic_id, status, label, basis, first, birth, detected,
             score, confidence, metrics, gates, limitations) = row
            title_diagnostic_version = (
                ((metrics or {}).get("observed") or {})
                .get("paper_title_diagnostics") or {}
            ).get("version")
            if title_diagnostic_version not in (
                None, "title-evidence-diagnostic-v1", "title-evidence-diagnostic-v2"
            ):
                raise ValueError("Неизвестная версия сохранённой диагностики названий.")
            title_assessor = (
                assess_title if title_diagnostic_version == "title-evidence-diagnostic-v2"
                else assess_title_v1
            )
            cur.execute(
                "SELECT work_id FROM topic_membership WHERE topic_id=%s ORDER BY work_id",
                (topic_id,),
            )
            composition_work_ids = [work_id for work_id, in cur.fetchall()]
            cur.execute(
                """
                SELECT e.evidence_rank, e.role, e.rationale, e.sources,
                       w.canonical_title, w.effective_date,
                       ARRAY(
                           SELECT DISTINCT r.payload->>'type'
                           FROM work_version v
                           JOIN raw_record r ON r.raw_record_id = v.raw_record_id
                           WHERE v.work_id = e.work_id AND v.source = 'openalex'
                             AND NULLIF(r.payload->>'type', '') IS NOT NULL
                           ORDER BY 1
                       ) AS openalex_record_types
                FROM evidence_item e JOIN work w USING (work_id)
                WHERE e.candidate_id = %s ORDER BY e.evidence_rank
                """,
                (candidate_id,),
            )
            evidence = [
                {"rank": rank,
                 "role": (
                     "самая ранняя работа в выборке" if role == "раннее основание"
                     else "пример из последнего окна" if role == "рост последнего окна"
                     else role
                 ),
                 "saved_role": role,
                 "title_diagnostic": title_assessor(title, label),
                 "openalex_record_types": record_types,
                 "rationale": rationale, "sources": sources,
                 "title": title, "published_at": published.isoformat()}
                for rank, role, rationale, sources, title, published, record_types in cur.fetchall()
            ]
            cards.append({
                "candidate_id": candidate_id,
                "topic_id": topic_id,
                "composition_sha256": composition_sha256(composition_work_ids),
                "status": status, "label": label,
                "label_basis": basis,
                "first_found": first.isoformat() if first else None,
                "research_birth": birth.isoformat() if birth else None,
                "signal_detected": detected.isoformat() if detected else None,
                "emergence_score": score, "evidence_confidence": confidence,
                "metrics": metrics, "gates": gates,
                "limitations": list(limitations), "evidence": evidence,
            })
    return {"mission_id": mission_id, "score_run_id": score_run_id,
            "provenance": provenance, "cards": cards}


def main() -> int:
    parser = argparse.ArgumentParser(description="Метрики и карточки кандидатов Horizon")
    parser.add_argument("mission_id")
    parser.add_argument("--export", type=Path, help="новый путь JSON для карточек")
    parser.add_argument("--score-run-id", type=int,
                        help="явный завершённый score-прогон для export-only")
    parser.add_argument("--export-only", action="store_true",
                        help="не создавать score-прогон, только выгрузить сохранённый")
    parser.add_argument("--historical-novelty-report", type=Path,
                        help="проверенный диагностический JSON исторического фона")
    parser.add_argument("--historical-novelty-sensitivity", type=Path,
                        help="проверка устойчивости диагностического JSON")
    args = parser.parse_args()
    if args.export_only and args.export is None:
        parser.error("Для --export-only нужен новый путь --export.")
    if not args.export_only and args.score_run_id is not None:
        parser.error("--score-run-id используется только вместе с --export-only.")
    if args.export is not None and args.export.exists():
        parser.error("Файл уже существует; выберите новый версионированный путь.")
    if args.export_only:
        result = {"run_id": args.score_run_id}
    else:
        result = build_candidates(
            args.mission_id,
            historical_novelty_report=args.historical_novelty_report,
            historical_novelty_sensitivity=args.historical_novelty_sensitivity,
        )
        print(result)
    if args.export:
        payload = export_cards(args.mission_id, args.score_run_id or result["run_id"])
        args.export.parent.mkdir(parents=True, exist_ok=True)
        with args.export.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(f"карточки: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
