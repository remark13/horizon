"""Sensitivity audit for historical-background novelty rankings."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from saia import db, methodology
from saia.candidates import parse_vector
from saia.historical_background import assess_current_anchors, load, topic_anchors
from saia.package_observations import payload_hash


VERSION = "historical-novelty-sensitivity-0.4.42-r1"


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def summarise(variants: list[dict]) -> list[dict]:
    keys = sorted(variants[0]["topics"], key=int)
    result = []
    for key in keys:
        values = [item["topics"][key]["novelty_percentile"] for item in variants]
        raw = [item["topics"][key]["novelty_raw"] for item in variants]
        if any(value is None for value in values):
            raise ValueError("Sensitivity variants require available novelty percentiles")
        result.append({
            "topic_id": int(key),
            "percentile_min": min(values),
            "percentile_median": float(np.median(values)),
            "percentile_max": max(values),
            "raw_min": min(raw),
            "raw_median": float(np.median(raw)),
            "raw_max": max(raw),
            "top_rank_count": sum(value == max(
                variant["topics"][other]["novelty_percentile"]
                for other in variant["topics"]
            ) for value, variant in zip(values, variants, strict=True)),
            "nearest_background_topics": dict(Counter(
                str(item["topics"][key]["nearest_background_topic"])
                for item in variants
            )),
        })
    return result


def validated_score_values(diagnostic_path: Path, sensitivity_path: Path,
                           *, cluster_run_id: int, model: str,
                           topic_ids: set[int]) -> tuple[dict[int, dict], dict]:
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    sensitivity = json.loads(sensitivity_path.read_text(encoding="utf-8"))
    for name, payload in (("diagnostic", diagnostic), ("sensitivity", sensitivity)):
        claimed = payload.get("payload_sha256")
        if claimed != payload_hash({key: value for key, value in payload.items()
                                   if key != "payload_sha256"}):
            raise ValueError(f"Historical novelty {name} hash mismatch")
    if diagnostic.get("status") != "diagnostic_not_applied_to_score":
        raise ValueError("Historical novelty diagnostic has an unsupported status")
    if sensitivity.get("status") != "sensitivity_diagnostic_not_applied_to_score":
        raise ValueError("Historical novelty sensitivity has an unsupported status")
    if diagnostic.get("cluster_run_id") != cluster_run_id or sensitivity.get("cluster_run_id") != cluster_run_id:
        raise ValueError("Historical novelty evidence belongs to another cluster run")
    if diagnostic["provenance"].get("embedding_model") != model:
        raise ValueError("Historical novelty evidence uses another embedding model")
    if sensitivity["input"].get("diagnostic_payload_sha256") != diagnostic["payload_sha256"]:
        raise ValueError("Sensitivity report belongs to another novelty diagnostic")
    if not diagnostic["provenance"].get("current_membership_subset_of_target_pack"):
        raise ValueError("Current topic membership was not proven inside target pack")
    if not diagnostic["interpretation"].get(
            "controlled_historical_relevance_predicate_reapplied"):
        raise ValueError("Historical relevance predicate was not proven")
    diagnostic_topics = {int(item["topic_id"]): item for item in diagnostic["topics"]}
    summary = {int(item["topic_id"]): item for item in sensitivity["summary"]}
    if set(diagnostic_topics) != topic_ids or set(summary) != topic_ids:
        raise ValueError("Historical novelty evidence topic set differs from cluster run")
    values = {}
    for topic_id in sorted(topic_ids):
        base, stability = diagnostic_topics[topic_id], summary[topic_id]
        stable = stability["percentile_min"] == stability["percentile_max"]
        values[topic_id] = {
            "novelty_raw": base["novelty_raw"],
            "novelty_percentile": base["novelty_percentile"] if stable else None,
            "novelty_availability": (
                "available_historical_background_sensitivity_stable"
                if stable else "historical_background_parameter_sensitivity_unresolved"
            ),
            "nearest_background_topic": base["nearest_background_topic"],
            "nearest_background_similarity": base["nearest_background_similarity"],
            "nearest_background_terms": base["nearest_background_terms"],
            "historical_anchor_topics": base["historical_anchor_topics"],
            "sensitivity_percentile_min": stability["percentile_min"],
            "sensitivity_percentile_max": stability["percentile_max"],
            "sensitivity_variants": sensitivity["input"]["variants"],
        }
    provenance = {
        "diagnostic_payload_sha256": diagnostic["payload_sha256"],
        "sensitivity_payload_sha256": sensitivity["payload_sha256"],
        "background_manifest_payload_sha256": diagnostic["provenance"][
            "background_manifest_payload_sha256"
        ],
        "policy": "only_exactly_stable_percentile_across_all_sensitivity_variants",
    }
    return values, provenance


def run(background_dir: Path, diagnostic_path: Path, cluster_run_id: int,
        output: Path, *, min_topic_sizes: list[int], seeds: list[int]) -> dict:
    if output.exists():
        raise FileExistsError("Sensitivity output already exists")
    manifest, _ = load(background_dir)
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    claimed = diagnostic.get("payload_sha256")
    if claimed != payload_hash({key: value for key, value in diagnostic.items()
                               if key != "payload_sha256"}):
        raise ValueError("Novelty diagnostic hash mismatch")
    if diagnostic.get("cluster_run_id") != cluster_run_id:
        raise ValueError("Diagnostic belongs to another cluster run")
    if diagnostic["provenance"]["background_manifest_payload_sha256"] != manifest["payload_sha256"]:
        raise ValueError("Diagnostic belongs to another historical background")
    if not min_topic_sizes or any(type(value) is not int or value < 2 for value in min_topic_sizes):
        raise ValueError("min_topic_sizes must contain integers >= 2")
    if not seeds or any(type(value) is not int for value in seeds):
        raise ValueError("seeds must contain integers")

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.topic_id,t.label,t.anchor_embedding::text,"
            "array_agg(DISTINCT v.source_record_id ORDER BY v.source_record_id) "
            "FROM topic t JOIN topic_membership tm USING(topic_id) "
            "JOIN work_version v ON v.work_id=tm.work_id AND v.source='arxiv' "
            "WHERE t.run_id=%s GROUP BY t.topic_id,t.label,t.anchor_embedding "
            "ORDER BY t.topic_id", (cluster_run_id,),
        )
        current_rows = cur.fetchall()
    if stable_hash([list(row) for row in current_rows]) != diagnostic["provenance"]["current_topics_payload_sha256"]:
        raise ValueError("Current topic composition differs from diagnostic input")
    current = {str(topic_id): parse_vector(vector).tolist()
               for topic_id, _, vector, _ in current_rows}

    table = pq.read_table(background_dir / "historical_documents.parquet")
    rows = [{
        "arxiv_id": row["arxiv_id"], "published": row["published"],
        "title": row["title"], "abstract": row["abstract"],
    } for row in table.to_pylist()]
    vectors = table["embedding"].to_pylist()
    cfg = methodology.load_default().clustering
    variants = []
    for size in min_topic_sizes:
        for seed in seeds:
            anchors, labels = topic_anchors(
                rows, vectors, min_topic_size=size, seed=seed,
                umap=dict(cfg["umap"]), hdbscan=dict(cfg["hdbscan"]),
                small_window_similarity_threshold=float(
                    cfg.get("graph", {}).get("similarity_threshold", 0.35)
                ),
            )
            if not anchors:
                raise ValueError(f"No anchors for sensitivity variant size={size}, seed={seed}")
            variants.append({
                "min_topic_size": size,
                "seed": seed,
                "background_topics": len(anchors),
                "clustered_documents": sum(label != -1 for label in labels),
                "noise_documents": sum(label == -1 for label in labels),
                "topics": assess_current_anchors(current, anchors),
            })
    summary = summarise(variants)
    report = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "sensitivity_diagnostic_not_applied_to_score",
        "cluster_run_id": cluster_run_id,
        "input": {
            "background_manifest_payload_sha256": manifest["payload_sha256"],
            "diagnostic_payload_sha256": diagnostic["payload_sha256"],
            "min_topic_sizes": min_topic_sizes,
            "seeds": seeds,
            "variants": len(variants),
        },
        "variants": variants,
        "summary": summary,
        "interpretation": {
            "changes_existing_candidate_score": False,
            "tests_clustering_parameter_sensitivity": True,
            "proves_detection_quality": False,
        },
    }
    report["payload_sha256"] = payload_hash(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background-dir", type=Path, required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--cluster-run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-topic-size", type=int, action="append", required=True)
    parser.add_argument("--seed", type=int, action="append", required=True)
    args = parser.parse_args(argv)
    report = run(
        args.background_dir, args.diagnostic, args.cluster_run_id, args.output,
        min_topic_sizes=args.min_topic_size, seeds=args.seed,
    )
    print(json.dumps({
        "version": report["version"], "status": report["status"],
        "input": report["input"], "summary": report["summary"],
        "payload_sha256": report["payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
