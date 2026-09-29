"""Read-only complete-link split diagnostic on saved BAS SPECTER2 vectors."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np

from saia import db
from saia.candidates import parse_vector
from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-complete-link-split-pilot-v1"


def bound_json(relative: str, expected_hash: str) -> dict:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != expected_hash:
        raise ValueError("Frozen input differs")
    return json.loads(path.read_text(encoding="utf-8"))


def complete_link(ids: list[int], similarities: np.ndarray,
                  threshold: float) -> list[list[int]]:
    """Merge only if *every* cross-pair meets the threshold; no chain links."""
    if (len(ids) != len(set(ids)) or similarities.shape != (len(ids), len(ids))
            or not np.isfinite(similarities).all() or not -1 <= threshold <= 1):
        raise ValueError("Invalid clustering inputs")
    clusters = [(index,) for index in range(len(ids))]
    while True:
        best: tuple[float, int, int] | None = None
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                minimum = min(float(similarities[a, b])
                              for a in clusters[left] for b in clusters[right])
                candidate = (minimum, -left, -right)
                if minimum >= threshold and (best is None or candidate > best):
                    best = candidate
        if best is None:
            break
        left, right = -best[1], -best[2]
        joined = tuple(sorted((*clusters[left], *clusters[right])))
        clusters = [cluster for index, cluster in enumerate(clusters)
                    if index not in {left, right}]
        clusters.append(joined)
        clusters.sort(key=lambda cluster: min(ids[index] for index in cluster))
    return sorted((sorted(ids[index] for index in cluster) for cluster in clusters),
                  key=lambda group: (-len(group), group))


def load_vectors(source: dict, model: str) -> dict[int, np.ndarray]:
    works = {work["work_id"]: work for card in source["cards"][:15]
             for work in card["works"]}
    ids = sorted(works)
    with db.connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT w.work_id,w.canonical_title,w.abstract,e.dim,e.embedding::text "
                "FROM work w JOIN work_embedding e USING(work_id) "
                "WHERE w.work_id=ANY(%s) AND w.mission_id=%s AND e.model=%s",
                (ids, source["mission_id"], model),
            )
            rows = cur.fetchall()
    if len(rows) != len(ids):
        raise ValueError("Saved vectors are incomplete")
    result = {}
    for identifier, title, abstract, dim, vector_text in rows:
        frozen = works[identifier]
        if title != frozen["title"] or (abstract or "") != frozen["abstract"]:
            raise ValueError("Embedding input text differs from frozen source")
        vector = parse_vector(vector_text)
        if (vector is None or dim != 768 or len(vector) != dim
                or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0):
            raise ValueError("Invalid saved vector")
        result[identifier] = vector / np.linalg.norm(vector)
    return result


def evaluate(config: dict, source: dict, contrast: dict, task_pairs: dict,
             threshold_report: dict, vectors: dict[int, np.ndarray]) -> dict:
    if (config.get("version") != VERSION or config.get("production_use") is not False
            or config.get("method") !=
            "complete_link_cosine_with_existing_frozen_development_threshold"
            or config["model"] != threshold_report["embedding_model"]
            or threshold_report["selected_feature"] != "specter_cosine"):
        raise ValueError("Unknown or production-enabled split protocol")
    threshold = float(threshold_report["selected_threshold"])
    cards = source["cards"][:15]
    expected = {work["work_id"] for card in cards for work in card["works"]}
    if len(expected) != sum(len(card["works"]) for card in cards) or set(vectors) != expected:
        raise ValueError("Expected one unique vector per top-15 work")
    groups_by_rank = {}
    rows = []
    for card in cards:
        ids = [work["work_id"] for work in card["works"]]
        matrix = np.stack([vectors[identifier] for identifier in ids])
        groups = complete_link(ids, matrix @ matrix.T, threshold)
        groups_by_rank[card["rank"]] = groups
        rows.append({"rank": card["rank"], "candidate_id": card["candidate_id"],
                     "original_work_count": len(ids), "group_sizes": [len(x) for x in groups],
                     "groups": groups})
    card_by_work = {work["work_id"]: card["rank"]
                    for card in cards for work in card["works"]}

    def together(a: int, b: int) -> bool:
        if a not in card_by_work or b not in card_by_work or card_by_work[a] != card_by_work[b]:
            raise ValueError("Pair outside one top-15 parent card")
        return any({a, b} <= set(group) for group in groups_by_rank[card_by_work[a]])

    contrast_rows = []
    for row in contrast["rows"]:
        a, b = [work["work_id"] for work in row["works"]]
        contrast_rows.append({"rank": row["rank"], "a": a, "b": b,
                              "contrast_type": row["contrast_type"],
                              "still_together": together(a, b)})
    controls = []
    for pair in task_pairs["pairs"]:
        controls.append({"a": pair["a"], "b": pair["b"],
                         "same_task": pair["same_task"],
                         "still_together": together(pair["a"], pair["b"])})
    all_groups = [group for groups in groups_by_rank.values() for group in groups]
    multi = [group for group in all_groups if len(group) >= 2]
    triple = [group for group in all_groups if len(group) >= 3]
    positives = [row for row in controls if row["same_task"]]
    negatives = [row for row in controls if not row["same_task"]]
    vector_sha256 = hashlib.sha256(b"".join(
        str(identifier).encode() + b":" + np.asarray(vectors[identifier], dtype="<f8").tobytes()
        for identifier in sorted(vectors))).hexdigest()
    return {"version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": config["source_sha256"],
            "input_vector_sha256": vector_sha256,
            "model": config["model"], "threshold": threshold,
            "counts": {
                "original_cards": len(cards), "original_works": len(expected),
                "new_groups_including_singletons": len(all_groups),
                "groups_with_two_or_more": len(multi),
                "groups_with_three_or_more": len(triple),
                "works_in_two_or_more": sum(map(len, multi)),
                "works_in_three_or_more": sum(map(len, triple)),
                "decisive_contrasts_separated": sum(
                    not row["still_together"] for row in contrast_rows
                    if row["contrast_type"] != "parent_family_only"),
                "decisive_contrasts_total": sum(
                    row["contrast_type"] != "parent_family_only" for row in contrast_rows),
                "same_task_controls_retained": sum(row["still_together"] for row in positives),
                "same_task_controls_total": len(positives),
                "different_task_controls_separated": sum(
                    not row["still_together"] for row in negatives),
                "different_task_controls_total": len(negatives),
            },
            "group_size_distribution": dict(Counter(map(len, all_groups))),
            "cards": rows, "contrast_pairs": contrast_rows,
            "existing_task_pair_controls": controls,
            "limitations": config["limitations"],
            "new_top15_quality_measured": False,
            "growth_or_diffusion_recomputed": False,
            "production_changed": False}


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source = bound_json(config["source"], config["source_sha256"])
    contrast = bound_json(config["contrast_report"], config["contrast_report_sha256"])
    task_pairs = bound_json(config["task_pair_protocol"],
                            config["task_pair_protocol_sha256"])
    threshold = bound_json(config["threshold_report"],
                           config["threshold_report_sha256"])
    result = evaluate(config, source, contrast, task_pairs, threshold,
                      load_vectors(source, config["model"]))
    result["config_sha256"] = sha256_file(config_path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Split report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
