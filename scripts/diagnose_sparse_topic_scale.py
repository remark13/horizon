"""Read-only sensitivity check for an already saved sparse analysis cohort.

Uses the same embeddings, BERTopic settings and fixed seed as the production
global reconstruction. It writes no cluster/score rows and is not calibration.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

from saia import db, methodology
from saia.cluster import cluster_window, cosine_matrix
from saia.publication_versions import find_version_families


VERSION = "sparse-topic-scale-diagnostic-v2"


def family_representative_indices(records: list[tuple], families: list[dict]) -> tuple[list[int], list[dict]]:
    """Select earliest records for sensitivity only; retain all source IDs in audit."""
    position = {record[0]: index for index, record in enumerate(records)}
    if len(position) != len(records):
        raise ValueError("Diagnostic cohort contains duplicate work IDs")
    removed = set()
    seen = set()
    audit = []
    for family in families:
        work_ids = family["work_ids"]
        if len(work_ids) < 2 or any(identifier not in position for identifier in work_ids):
            raise ValueError("Version family does not match diagnostic cohort")
        if len(work_ids) != len(set(work_ids)) or seen.intersection(work_ids):
            raise ValueError("Overlapping version families")
        seen.update(work_ids)
        representative = min(work_ids, key=lambda identifier: (
            records[position[identifier]][3], identifier))
        suppressed = sorted(set(work_ids) - {representative})
        removed.update(suppressed)
        audit.append({"representative_work_id": representative,
                      "all_source_work_ids": sorted(work_ids),
                      "omitted_from_sensitivity_only": suppressed})
    return [index for index, record in enumerate(records) if record[0] not in removed], audit


def _cluster_scales(texts: list[str], vectors: np.ndarray, ids: list[int],
                    titles: list[str], clustering: dict, config) -> list[dict]:
    hdbscan = dict(clustering["hdbscan"])
    hdbscan["cluster_selection_method"] = clustering.get(
        "global_hdbscan_selection_method", hdbscan["cluster_selection_method"])
    scale_rows = []
    for scale in ("seed", "micro"):
        min_size = int(clustering["scales"][scale]["min_topic_size"])
        labels, terms = cluster_window(
            texts, vectors, min_size, int(clustering["seed"]),
            dict(clustering["umap"]), dict(hdbscan), config.link_threshold)
        communities = []
        for label in sorted(set(labels.tolist()) - {-1}):
            members = [index for index, value in enumerate(labels) if value == label]
            communities.append({"label": int(label), "work_ids": [ids[i] for i in members],
                                "titles": [titles[i] for i in members],
                                "terms": terms.get(label, [])})
        scale_rows.append({"scale": scale, "min_topic_size": min_size,
                           "topic_count": len(communities),
                           "noise_count": int((labels == -1).sum()),
                           "communities": communities})
    return scale_rows


def run(normalize_run_id: int, quality_generation_id: int, model: str) -> dict:
    if normalize_run_id < 1 or quality_generation_id < 1 or not model:
        raise ValueError("Exact positive run IDs and model are required")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT w.work_id,w.canonical_title,w.abstract,w.effective_date,e.embedding::text "
            "FROM work w JOIN work_embedding e USING(work_id) "
            "JOIN quality_snapshot q ON q.work_id=w.work_id "
            "WHERE w.run_id=%s AND q.generation_id=%s AND e.model=%s "
            "AND q.decision='include' ORDER BY w.effective_date,w.work_id",
            (normalize_run_id, quality_generation_id, model),
        )
        records = cur.fetchall()
        work_ids = [record[0] for record in records]
        cur.execute("SELECT work_id,value FROM identifier WHERE work_id=ANY(%s) "
                    "AND kind='doi' ORDER BY work_id,value", (work_ids,))
        doi_rows = cur.fetchall()
        cur.execute("SELECT wa.work_id,a.display_name FROM work_author wa "
                    "JOIN author a USING(author_id) WHERE wa.work_id=ANY(%s) "
                    "ORDER BY wa.work_id,wa.author_position", (work_ids,))
        author_rows = cur.fetchall()
    if not records:
        raise ValueError("No matching quality-included embedded works")
    ids = [record[0] for record in records]
    titles = [record[1] for record in records]
    texts = [f"{record[1]} {record[2] or ''}".strip() for record in records]
    dates = [record[3].isoformat() for record in records]
    vectors = np.vstack([np.fromstring(record[4].strip("[]"), sep=",",
                                         dtype=np.float32) for record in records])
    if len(set(ids)) != len(ids) or not np.isfinite(vectors).all():
        raise ValueError("Invalid saved embedding cohort")
    config = methodology.load_default()
    clustering = config.clustering
    scale_rows = _cluster_scales(texts, vectors, ids, titles, clustering, config)
    similarities = cosine_matrix(vectors, vectors)
    pair_values = [float(similarities[i, j]) for i in range(len(ids))
                   for j in range(i + 1, len(ids))]
    strongest = sorted(((float(similarities[i, j]), i, j)
                        for i in range(len(ids)) for j in range(i + 1, len(ids))),
                       reverse=True)[:20]
    normalized_title_counts = Counter(" ".join(title.casefold().split())
                                      for title in titles)
    dois = {identifier: value for identifier, value in doi_rows}
    authors: dict[int, list[str]] = {}
    for identifier, name in author_rows:
        authors.setdefault(identifier, []).append(name)
    version_candidates = find_version_families([
        {"work_id": record[0], "title": record[1], "abstract": record[2],
         "published_at": record[3].isoformat(), "doi": dois.get(record[0]),
         "authors": authors.get(record[0], [])}
        for record in records
    ])
    representative_indices, family_audit = family_representative_indices(
        records, version_candidates["families"])
    representative_scales = _cluster_scales(
        [texts[i] for i in representative_indices], vectors[representative_indices],
        [ids[i] for i in representative_indices],
        [titles[i] for i in representative_indices], clustering, config)
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "normalize_run_id": normalize_run_id,
            "quality_generation_id": quality_generation_id,
            "embedding_model": model, "methodology_sha256": config.config_hash,
            "cohort": [{"work_id": identifier, "title": title, "effective_date": day}
                       for identifier, title, day in zip(ids, titles, dates)],
            "same_title_repetitions": {title: count for title, count in
                                       normalized_title_counts.items() if count > 1},
            "candidate_version_families": version_candidates,
            "family_representative_sensitivity": {
                "unit_count": len(representative_indices),
                "family_audit": family_audit,
                "scales": representative_scales,
                "not_a_production_deduplication": True},
            "pairwise_cosine": {"pair_count": len(pair_values),
                                "min": min(pair_values),
                                "median": float(np.median(pair_values)),
                                "max": max(pair_values),
                                "top_pairs": [{"cosine": score, "left_work_id": ids[i],
                                               "right_work_id": ids[j],
                                               "left_title": titles[i],
                                               "right_title": titles[j]}
                                              for score, i, j in strongest]},
            "scales": scale_rows,
            "policy": {"same_cohort_sensitivity_not_independent_evaluation": True,
                       "diagnostic_does_not_write_score_or_cards": True,
                       "seed_topics_not_automatically_weak_signals": True}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--normalize-run-id", type=int, required=True)
    parser.add_argument("--quality-generation-id", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Diagnostic output is immutable")
    report = run(args.normalize_run_id, args.quality_generation_id, args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"works": len(report["cohort"]),
                      "scales": {row["scale"]: row["topic_count"]
                                 for row in report["scales"]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
