"""Development-only diagnostic of time-respecting SPECTER2 neighbours.

The graph is constructed without catalog labels.  Development references are
joined only afterwards to test whether semantic proximity can bridge papers
that BERTopic placed in different topics or noise.  No result is written into
production topics, scores, or statuses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import yaml

from saia import db, evaluation_catalog, runs
from saia.benchmark import identifier_url


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "temporal-neighborhood.v0.4.19.yaml"
REPORT_VERSION = "temporal-semantic-neighborhood-0.4.19"


def payload_hash(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_policy(path: Path = DEFAULT_POLICY) -> dict:
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    neighbors = policy.get("neighbors", {})
    if policy.get("status") != "development_only":
        raise ValueError("Диагностика должна быть явно development_only.")
    if neighbors.get("direction") != "prior_only":
        raise ValueError("Разрешены только связи с уже видимыми работами.")
    maximum = neighbors.get("max_prior_neighbors")
    ks = neighbors.get("k_values") or []
    if not isinstance(maximum, int) or maximum < 1 or not ks:
        raise ValueError("Нужны положительные max_prior_neighbors и k_values.")
    if any(not isinstance(k, int) or not 1 <= k <= maximum for k in ks):
        raise ValueError("Каждый k должен быть от 1 до max_prior_neighbors.")
    thresholds = neighbors.get("absolute_cosine_thresholds") or []
    quantiles = neighbors.get("background_edge_quantiles") or []
    if any(not 0 <= value <= 1 for value in thresholds + quantiles):
        raise ValueError("Пороги и квантили должны находиться в [0,1].")
    if policy.get("guardrails", {}).get("reserved_evaluated") is not False:
        raise ValueError("Reserved-кейсы нельзя открывать в этой диагностике.")
    return policy


def normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or not len(matrix):
        raise ValueError("Нужна непустая двумерная матрица эмбеддингов.")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0) or not np.isfinite(matrix).all():
        raise ValueError("Эмбеддинги должны быть конечными и ненулевыми.")
    return matrix / norms


def temporal_neighbors(work_ids: list[int], effective_dates: list[date],
                       vectors: np.ndarray, *, max_neighbors: int,
                       max_gap_days: int, block_size: int = 256) -> list[list[tuple[int, float]]]:
    """Return top prior row indices for every row, ordered by similarity.

    Rows are sorted internally by (date, work_id). A same-day edge is oriented
    by work_id solely to keep the graph acyclic and reproducible.
    """
    if len(work_ids) != len(effective_dates) or len(work_ids) != len(vectors):
        raise ValueError("work_ids, dates и vectors должны иметь одинаковую длину.")
    if len(set(work_ids)) != len(work_ids):
        raise ValueError("work_id не должны повторяться.")
    if max_neighbors < 1 or max_gap_days < 0 or block_size < 1:
        raise ValueError("Параметры соседства должны быть положительными.")
    order = sorted(range(len(work_ids)), key=lambda i: (effective_dates[i], work_ids[i]))
    dates = [effective_dates[i] for i in order]
    matrix = normalize_vectors(np.asarray(vectors)[order])
    sorted_neighbors: list[list[tuple[int, float]]] = [[] for _ in order]
    for start in range(0, len(order), block_size):
        stop = min(start + block_size, len(order))
        similarities = matrix[start:stop] @ matrix.T
        for local, current in enumerate(range(start, stop)):
            if current == 0:
                continue
            earliest = 0
            while earliest < current and (dates[current] - dates[earliest]).days > max_gap_days:
                earliest += 1
            if earliest == current:
                continue
            # Float32 dot products can exceed 1 by a few ulps.  Persisting
            # 1.0000007 as a cosine value is misleading even though ranking is
            # unchanged, so clamp to the mathematical range before selection.
            row = np.clip(similarities[local, earliest:current], -1.0, 1.0)
            take = min(max_neighbors, len(row))
            if take == len(row):
                local_indices = np.arange(len(row))
            else:
                local_indices = np.argpartition(row, -take)[-take:]
            ranked = sorted(
                ((earliest + int(idx), float(row[int(idx)])) for idx in local_indices),
                key=lambda item: (-item[1], work_ids[order[item[0]]]),
            )
            original_current = order[current]
            sorted_neighbors[original_current] = [
                (order[prior], similarity) for prior, similarity in ranked
            ]
    return sorted_neighbors


def filtered_edges(neighbors: list[list[tuple[int, float]]], *, k: int,
                   threshold: float) -> list[tuple[int, int, float]]:
    return [(later, prior, similarity)
            for later, items in enumerate(neighbors)
            for prior, similarity in items[:k] if similarity >= threshold]


def component_summary(node_count: int, edges: Iterable[tuple[int, int, float]]) -> tuple[dict, list[int]]:
    parent = list(range(node_count))
    size = [1] * node_count

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right, _similarity in edges:
        a, b = find(left), find(right)
        if a == b:
            continue
        if size[a] < size[b]:
            a, b = b, a
        parent[b] = a
        size[a] += size[b]
    roots = [find(node) for node in range(node_count)]
    counts = sorted(Counter(roots).values(), reverse=True)
    non_singletons = [value for value in counts if value > 1]
    summary = {
        "components": len(counts),
        "non_singleton_components": len(non_singletons),
        "singletons": sum(value == 1 for value in counts),
        "largest_component": counts[0] if counts else 0,
        "largest_component_share": round((counts[0] / node_count), 6) if counts else 0.0,
        "median_non_singleton_size": (
            float(np.median(non_singletons)) if non_singletons else None
        ),
        "p95_non_singleton_size": (
            float(np.percentile(non_singletons, 95)) if non_singletons else None
        ),
    }
    return summary, roots


def evaluate_cases(cases: list[dict], id_to_index: dict[str, int],
                   work_rows: list[dict], neighbors: list[list[tuple[int, float]]],
                   edges: list[tuple[int, int, float]], roots: list[int],
                   *, k: int) -> list[dict]:
    edge_pairs = {frozenset((left, right)): similarity for left, right, similarity in edges}
    results = []
    for case in cases:
        present = [(identifier, id_to_index[identifier]) for identifier in case["arxiv_ids"]
                   if identifier in id_to_index]
        pairs = []
        for offset, (left_id, left) in enumerate(present):
            for right_id, right in present[offset + 1:]:
                similarity = edge_pairs.get(frozenset((left, right)))
                if similarity is not None:
                    pairs.append({"left": left_id, "right": right_id,
                                  "cosine_similarity": round(similarity, 6)})
        component_counts = Counter(roots[index] for _identifier, index in present)
        max_same_component = max(component_counts.values(), default=0)
        publications = []
        family_indices = {index for _identifier, index in present}
        for identifier, index in present:
            nearest = []
            for prior, similarity in neighbors[index][:k]:
                row = work_rows[prior]
                nearest.append({
                    "arxiv_id": row.get("arxiv_id"),
                    "title": row["title"],
                    "effective_date": row["effective_date"].isoformat(),
                    "cosine_similarity": round(similarity, 6),
                    "same_catalog_family": prior in family_indices,
                })
            publications.append({"arxiv_id": identifier,
                                 "source_url": identifier_url("arxiv", identifier),
                                 "nearest_prior": nearest})
        results.append({
            "case_id": case["case_id"], "family_id": case["family_id"],
            "title": case["title"], "kind": case["kind"],
            "target_references": len(case["arxiv_ids"]),
            "eligible_references": len(present),
            "direct_catalog_pairs": pairs,
            "direct_pair_recovered": bool(pairs),
            "max_catalog_references_in_one_component": max_same_component,
            "same_component_recovered": max_same_component >= 2,
            "publications": publications,
        })
    return results


def _load_input(mission_id: str, score_run_id: int) -> tuple[dict, list[dict], np.ndarray]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT upstream_run_id,as_of_date,query_version_id FROM analysis_run "
            "WHERE run_id=%s AND mission_id=%s AND kind='score' AND status='done'",
            (score_run_id, mission_id),
        )
        score = cur.fetchone()
        if not score:
            raise ValueError("Завершённый score-прогон не найден.")
        cluster_run_id, as_of, query_version_id = score
        cur.execute(
            "SELECT upstream_run_id,embedding_model,notes FROM analysis_run "
            "WHERE run_id=%s AND mission_id=%s AND kind='cluster' AND status='done'",
            (cluster_run_id, mission_id),
        )
        cluster = cur.fetchone()
        if not cluster:
            raise ValueError("Завершённый cluster-вход не найден.")
        normalize_run_id, model, notes = cluster
        generation_id = (notes or {}).get("quality_generation_id")
        if not generation_id or not model:
            raise ValueError("Cluster не закрепил качество или модель эмбеддингов.")
        cur.execute(
            """
            SELECT w.work_id,w.effective_date,w.canonical_title,e.embedding::text,
                   arxiv.value
            FROM work w
            JOIN quality_snapshot q ON q.work_id=w.work_id AND q.generation_id=%s
            JOIN work_embedding e ON e.work_id=w.work_id AND e.model=%s
            LEFT JOIN LATERAL (
                SELECT lower(i.value) AS value FROM identifier i
                WHERE i.work_id=w.work_id AND i.run_id=w.run_id AND i.kind='arxiv'
                ORDER BY i.value LIMIT 1
            ) arxiv ON true
            WHERE w.run_id=%s AND q.decision='include' AND w.effective_date < %s
            ORDER BY w.effective_date,w.work_id
            """,
            (generation_id, model, normalize_run_id, as_of),
        )
        raw = cur.fetchall()
    if not raw:
        raise ValueError("Нет допущенных работ с векторами.")
    rows = [{"work_id": work_id, "effective_date": effective,
             "title": title, "arxiv_id": arxiv_id}
            for work_id, effective, title, _vector, arxiv_id in raw]
    vectors = np.vstack([np.fromstring(vector.strip("[]"), sep=",", dtype=np.float32)
                         for _work, _day, _title, vector, _arxiv in raw])
    provenance = {"mission_id": mission_id, "query_version_id": query_version_id,
                  "score_run_id": score_run_id, "cluster_run_id": cluster_run_id,
                  "normalize_run_id": normalize_run_id,
                  "quality_generation_id": generation_id,
                  "embedding_model": model, "as_of_date": as_of.isoformat()}
    return provenance, rows, vectors


def analyze(mission_id: str, score_run_id: int, *, policy_path: Path = DEFAULT_POLICY,
            catalog_path: Path = evaluation_catalog.CATALOG_PATH) -> dict:
    policy = load_policy(policy_path)
    provenance, rows, vectors = _load_input(mission_id, score_run_id)
    params = policy["neighbors"]
    neighbors = temporal_neighbors(
        [row["work_id"] for row in rows],
        [row["effective_date"] for row in rows], vectors,
        max_neighbors=params["max_prior_neighbors"],
        max_gap_days=params["max_gap_days"], block_size=params["block_size"],
    )
    # Labels enter only after all neighbours for all works have been fixed.
    catalog = evaluation_catalog.load(catalog_path)
    cases = [case for case in catalog["cases"]
             if case["split"] == "development"
             and case["as_of_date"] == provenance["as_of_date"]
             and case["kind"] in set(policy["evaluation"]["kinds"])]
    id_to_index = {row["arxiv_id"]: index for index, row in enumerate(rows)
                   if row.get("arxiv_id")}
    similarity_pool = np.array([similarity for items in neighbors for _prior, similarity in items],
                               dtype=np.float64)
    thresholds = [("absolute", float(value))
                  for value in params["absolute_cosine_thresholds"]]
    thresholds += [(f"background_q{int(quantile * 100)}",
                    float(np.quantile(similarity_pool, quantile)))
                   for quantile in params["background_edge_quantiles"]]
    experiments = []
    for k in params["k_values"]:
        for origin, threshold in thresholds:
            edges = filtered_edges(neighbors, k=k, threshold=threshold)
            components, roots = component_summary(len(rows), edges)
            case_results = evaluate_cases(cases, id_to_index, rows, neighbors,
                                          edges, roots, k=k)
            eligible_cases = [item for item in case_results
                              if item["eligible_references"] >= 2]
            experiments.append({
                "k": k, "threshold_origin": origin,
                "cosine_threshold": round(threshold, 8),
                "edge_count": len(edges),
                "nodes_with_prior_edge": len({left for left, _right, _sim in edges}),
                "components": components,
                "giant_component_warning": (
                    components["largest_component_share"]
                    >= policy["evaluation"]["giant_component_warning_share"]
                ),
                "eligible_multi_reference_cases": len(eligible_cases),
                "direct_pair_cases_recovered": sum(
                    item["direct_pair_recovered"] for item in eligible_cases),
                "same_component_cases_recovered": sum(
                    item["same_component_recovered"] for item in eligible_cases),
                "cases": case_results,
            })
    report = {
        "version": REPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance,
        "policy": policy,
        "policy_bytes_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "policy_payload_sha256": payload_hash(policy),
        "catalog": evaluation_catalog.summary(catalog),
        "catalog_joined_after_graph_construction": True,
        "reserved_evaluated": False,
        "input": {"eligible_works": len(rows), "embedding_dimension": int(vectors.shape[1]),
                  "earliest_date": min(row["effective_date"] for row in rows).isoformat(),
                  "latest_date": max(row["effective_date"] for row in rows).isoformat(),
                  "neighbor_similarity": {
                      "count": int(len(similarity_pool)),
                      "min": round(float(similarity_pool.min()), 8),
                      "median": round(float(np.median(similarity_pool)), 8),
                      "p90": round(float(np.quantile(similarity_pool, .90)), 8),
                      "p95": round(float(np.quantile(similarity_pool, .95)), 8),
                      "p99": round(float(np.quantile(similarity_pool, .99)), 8),
                      "max": round(float(similarity_pool.max()), 8),
                  }},
        "experiments": experiments,
        "interpretation_rules": {
            "direct_pair_is_semantic_recovery_not_weak_signal": True,
            "same_component_is_invalid_when_giant_component_warning": True,
            "best_development_threshold_is_not_production_threshold": True,
            "precision_measured": False,
            "future_success_measured": False,
        },
        "limitations": [
            "Development-кейсы не являются экспертным gold и применены только после построения графа.",
            "В корпусе с двумя допущенными ссылками представлены лишь отдельные семейства; оценка имеет малую мощность.",
            "Текущие SPECTER2-векторы построены по current metadata; поздние редакции исключены quality screen, но v1-тексты не восстановлены.",
            "Связность не доказывает новизну, рост, независимость групп, рыночный спрос или будущий успех.",
        ],
        "code_version": runs.code_version(),
        "runtime": runs.runtime_snapshot(),
    }
    report["report_payload_sha256"] = payload_hash(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Диагностика временных SPECTER2-соседей")
    parser.add_argument("mission_id")
    parser.add_argument("--score-run", type=int, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--catalog", type=Path, default=evaluation_catalog.CATALOG_PATH)
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    if args.export.exists():
        raise ValueError("Отчёт уже существует и не перезаписывается.")
    report = analyze(args.mission_id, args.score_run,
                     policy_path=args.policy.resolve(), catalog_path=args.catalog.resolve())
    args.export.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    compact = [{"k": item["k"], "origin": item["threshold_origin"],
                "threshold": item["cosine_threshold"], "edges": item["edge_count"],
                "largest_share": item["components"]["largest_component_share"],
                "direct": item["direct_pair_cases_recovered"],
                "component": item["same_component_cases_recovered"]}
               for item in report["experiments"]]
    print(json.dumps({"input": report["input"], "experiments": compact,
                      "sha256": report["report_payload_sha256"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
