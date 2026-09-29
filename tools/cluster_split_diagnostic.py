"""Read-only diagnostic of whether displayed SAIA topics admit smaller groups.

This is not a second candidate generator. It reports within-topic vector
structure and short title samples so a developer can inspect over-grouping.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score

from saia import db


def examine(score_run_id: int) -> list[dict]:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT s.candidate_id,s.topic_id,s.label,r.embedding_model "
            "FROM signal_candidate s JOIN analysis_run r ON r.run_id=s.run_id "
            "WHERE s.run_id=%s ORDER BY s.candidate_id", (score_run_id,))
        cards = cur.fetchall()
        report = []
        for candidate_id, topic_id, label, model in cards:
            cur.execute(
                "SELECT DISTINCT ON(w.work_id) w.work_id,w.canonical_title,e.embedding::text "
                "FROM topic_membership tm JOIN work w USING(work_id) "
                "JOIN work_embedding e ON e.work_id=w.work_id AND e.model=%s "
                "WHERE tm.topic_id=%s ORDER BY w.work_id",
                (model, topic_id),
            )
            rows = cur.fetchall()
            if len(rows) < 6:
                report.append({"candidate_id": candidate_id, "label": label,
                               "documents": len(rows), "possible_split": False})
                continue
            vectors = np.vstack([np.fromstring(row[2].strip("[]"), sep=",")
                                 for row in rows])
            best = None
            for groups in range(2, min(5, len(rows) // 3) + 1):
                labels = AgglomerativeClustering(
                    n_clusters=groups, metric="cosine", linkage="average",
                ).fit_predict(vectors)
                counts = np.bincount(labels)
                if min(counts) < 3:
                    continue
                silhouette = float(silhouette_score(vectors, labels, metric="cosine"))
                if best is None or silhouette > best[0]:
                    best = (silhouette, labels, counts)
            if best is None:
                report.append({"candidate_id": candidate_id, "label": label,
                               "documents": len(rows), "possible_split": False})
                continue
            silhouette, labels, counts = best
            groups = [
                {"size": int(counts[group]),
                 "example_titles": [row[1] for index, row in enumerate(rows)
                                    if labels[index] == group][:4]}
                for group in range(len(counts))
            ]
            report.append({
                "candidate_id": candidate_id, "label": label, "documents": len(rows),
                "silhouette": round(silhouette, 3),
                "possible_split": silhouette >= 0.15, "groups": groups,
            })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("score_run_id", type=int)
    args = parser.parse_args()
    print(json.dumps(examine(args.score_run_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
