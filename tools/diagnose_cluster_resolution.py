"""Read-only diagnostic of topic resolution on a completed SAIA embedding run."""
from __future__ import annotations

import argparse
from collections import Counter

import numpy as np

from saia import db, methodology
from saia.cluster import cluster_window


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalize-run", type=int, required=True)
    parser.add_argument("--quality-generation", type=int, required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT w.canonical_title,w.abstract,e.embedding::text "
            "FROM work w JOIN work_embedding e USING (work_id) "
            "JOIN quality_snapshot q ON q.work_id=w.work_id AND q.generation_id=%s "
            "WHERE w.run_id=%s AND e.model=%s AND q.decision='include' "
            "ORDER BY w.effective_date,w.work_id",
            (args.quality_generation, args.normalize_run, args.model),
        )
        rows = cur.fetchall()
    texts = [f"{title} {abstract or ''}" for title, abstract, _ in rows]
    vectors = np.vstack([
        np.fromstring(vector.strip("[]"), sep=",", dtype=np.float32)
        for _, _, vector in rows
    ])
    cfg = methodology.load_default().raw["clustering"]
    print("works", len(rows), "dimensions", vectors.shape[1], flush=True)
    for selection in ("eom", "leaf"):
        labels, terms = cluster_window(
            texts, vectors, 5, 42, dict(cfg["umap"]),
            {"metric": "euclidean", "cluster_selection_method": selection},
            .35,
        )
        counts = Counter(int(label) for label in labels)
        print(selection, "topics", len(counts) - (-1 in counts),
              "noise", counts.get(-1, 0),
              "largest", sorted((size for key, size in counts.items() if key != -1), reverse=True)[:15],
              "terms", {str(key): words[:4] for key, words in terms.items()}, flush=True)


if __name__ == "__main__":
    main()
