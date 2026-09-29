"""Read-only, controlled topic-membership comparison for two saved runs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from saia import db


VERSION = "saved-cluster-quality-comparison-v1"


def compare(control_run_id: int, experiment_run_id: int) -> dict:
    if control_run_id == experiment_run_id or min(control_run_id, experiment_run_id) < 1:
        raise ValueError("Distinct positive run IDs required")
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("""
            SELECT run_id,mission_id,upstream_run_id,embedding_model,window_step,
                   clustering_scale,methodology_hash,notes->'effective_config',
                   (notes->>'quality_generation_id')::bigint
            FROM analysis_run WHERE run_id=ANY(%s) AND kind='cluster' AND status='done'
        """, ([control_run_id, experiment_run_id],))
        runs = {row[0]: row for row in cur.fetchall()}
        if set(runs) != {control_run_id, experiment_run_id}:
            raise ValueError("Both exact completed cluster runs are required")
        a, b = runs[control_run_id], runs[experiment_run_id]
        if a[1:6] != b[1:6]:
            raise ValueError("Different mission, normalized corpus, embedding, window or scale")
        config_a, config_b = deepcopy(a[7]), deepcopy(b[7])
        if not isinstance(config_a, dict) or not isinstance(config_b, dict):
            raise ValueError("Embedded methodology missing")
        mode_a = config_a.get("publication_quality", {}).pop("non_standalone_mode", "annotate")
        mode_b = config_b.get("publication_quality", {}).pop("non_standalone_mode", "annotate")
        if config_a != config_b or mode_a != "annotate" or mode_b != "exclude":
            raise ValueError("Runs differ by more than the predeclared supporting-type exclusion")
        generation_a, generation_b = a[8], b[8]
        cur.execute("""
            SELECT old.work_id,old.decision,new.decision,w.type
            FROM quality_snapshot old JOIN quality_snapshot new USING(work_id)
            JOIN work w USING(work_id)
            WHERE old.generation_id=%s AND new.generation_id=%s
        """, (generation_a, generation_b))
        decisions = cur.fetchall()
        transitions = Counter((before, after) for _, before, after, _ in decisions)
        changed = [(work_id, before, after, kind) for work_id, before, after, kind in decisions
                   if before != after]
        if any(before != "include" or after != "exclude" or kind not in
               ("peer-review", "erratum", "supplementary-materials")
               for _, before, after, kind in changed):
            raise ValueError("Quality changes exceed the predeclared source-type rule")
        keep = {work_id for work_id, _, after, _ in decisions if after == "include"}
        cur.execute("""
            SELECT t.run_id,t.topic_id,tm.work_id FROM topic t
            JOIN topic_membership tm USING(topic_id)
            WHERE t.run_id=ANY(%s)
        """, ([control_run_id, experiment_run_id],))
        topics = {control_run_id: defaultdict(set), experiment_run_id: defaultdict(set)}
        for run_id, topic_id, work_id in cur.fetchall():
            topics[run_id][topic_id].add(work_id)
    old = [(topic_id, works & keep) for topic_id, works in sorted(topics[control_run_id].items())]
    new = sorted(topics[experiment_run_id].items())
    if not old or not new:
        raise ValueError("One cluster run has no topics")
    similarity = np.array([
        [len(left & right) / len(left | right) if left | right else 0.0
         for _, right in new]
        for _, left in old
    ])
    old_idx, new_idx = linear_sum_assignment(-similarity)
    matches = [{"control_topic_id": old[i][0], "experiment_topic_id": new[j][0],
                "jaccard": round(float(similarity[i, j]), 6),
                "control_retained_works": len(old[i][1]),
                "experiment_works": len(new[j][1])}
               for i, j in zip(old_idx, new_idx)]
    scores = [match["jaccard"] for match in matches]
    return {
        "version": VERSION,
        "control_run_id": control_run_id, "experiment_run_id": experiment_run_id,
        "mission_id": a[1], "normalize_run_id": a[2], "embedding_model": a[3],
        "window_step": a[4], "clustering_scale": a[5],
        "methodology_hashes": {"control": a[6], "experiment": b[6]},
        "methodology_difference": "publication_quality.non_standalone_mode: annotate -> exclude only",
        "quality_generation_ids": {"control": generation_a, "experiment": generation_b},
        "decision_transitions": [{"control": before, "experiment": after, "works": count}
                                 for (before, after), count in sorted(transitions.items())],
        "excluded_work_types": dict(sorted(Counter(kind for _, _, _, kind in changed).items())),
        "topics": {"control": len(old), "experiment": len(new)},
        "matched_topics": len(matches),
        "jaccard_median": round(float(np.median(scores)), 6),
        "matched_jaccard_at_least_0_5": sum(score >= 0.5 for score in scores),
        "matched_jaccard_at_least_0_8": sum(score >= 0.8 for score in scores),
        "matches": matches,
        "interpretation": "Cluster sensitivity only; no score ranking or independent accuracy evaluated",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-run-id", type=int, required=True)
    parser.add_argument("--experiment-run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Comparison output is immutable")
    result = compare(args.control_run_id, args.experiment_run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in
                      ("topics", "excluded_work_types", "jaccard_median",
                       "matched_jaccard_at_least_0_5")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
