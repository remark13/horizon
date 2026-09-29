"""Read-only diagnostic: compare current topic anchors with pre-period history."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from saia import db, runs
from saia.candidates import parse_vector
from saia.historical_background import assess_current_anchors, load, sha256_file
from saia.package_observations import payload_hash
from saia.thematic_arxiv_cache import load_cache, load_target_packs


VERSION = "historical-novelty-diagnostic-0.4.42-r1"


def _stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _pack_ids(path: Path) -> set[str]:
    values = [str(value).strip() for value in pq.read_table(path, columns=["id"])["id"].to_pylist()]
    if not values or any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError("Target pack ids are empty or duplicated")
    return set(values)


def diagnose(cache_dir: Path, packs_dir: Path, background_dir: Path,
             cluster_run_id: int, output: Path) -> dict:
    if type(cluster_run_id) is not int or cluster_run_id < 1:
        raise ValueError("cluster_run_id must be a positive integer")
    if output.exists():
        raise FileExistsError("Diagnostic output already exists")
    background_manifest, anchors = load(background_dir)
    cache_manifest, _ = load_cache(cache_dir)
    if background_manifest["source"]["cache_manifest_sha256"] != cache_manifest["payload_sha256"]:
        raise ValueError("Background and configured thematic cache differ")
    packs_manifest = load_target_packs(packs_dir, cache_manifest["payload_sha256"])
    if background_manifest["source"]["target_pack_manifest_sha256"] != packs_manifest["payload_sha256"]:
        raise ValueError("Background and configured target packs differ")
    target_key = background_manifest["source"]["target_key"]
    matches = [item for item in packs_manifest["targets"] if item["target_key"] == target_key]
    if len(matches) != 1:
        raise ValueError("Background target pack is not uniquely available")
    target = matches[0]
    pack_path = packs_dir / target["file"]
    if sha256_file(pack_path) != background_manifest["source"]["target_pack_sha256"]:
        raise ValueError("Background target pack bytes differ")
    allowed_ids = _pack_ids(pack_path)

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT mission_id,embedding_model,upstream_run_id,status FROM analysis_run "
            "WHERE run_id=%s AND kind='cluster'", (cluster_run_id,),
        )
        run = cur.fetchone()
        if not run or run[3] != "done":
            raise ValueError("Cluster run is absent or incomplete")
        mission_id, model, normalize_run_id, _ = run
        if model != background_manifest["model"]["name"]:
            raise ValueError("Current topics and historical background use different models")
        period_start, period_end, period_origin = runs.analysis_period(cur, normalize_run_id)
        cutoff = date.fromisoformat(background_manifest["source"]["cutoff_exclusive"])
        if period_start != cutoff:
            raise ValueError("Historical cutoff must equal current corpus period start")
        cur.execute(
            "SELECT collection_batch_id FROM analysis_run WHERE run_id=%s",
            (normalize_run_id,),
        )
        batch_id = cur.fetchone()[0]
        cur.execute("SELECT coverage FROM collection_batch WHERE batch_id=%s", (batch_id,))
        coverage_row = cur.fetchone()
        source_mode = ((coverage_row[0] if coverage_row else {}).get("source_modes") or {}).get("arxiv") or {}
        if source_mode.get("upstream_inventory_sha256") != cache_manifest["source"]["inventory_sha256"]:
            raise ValueError("Current corpus and background use different arXiv inventories")

        cur.execute(
            "SELECT t.topic_id,t.label,t.anchor_embedding::text,"
            "array_agg(DISTINCT v.source_record_id ORDER BY v.source_record_id) "
            "FROM topic t JOIN topic_membership tm USING(topic_id) "
            "JOIN work_version v ON v.work_id=tm.work_id AND v.source='arxiv' "
            "WHERE t.run_id=%s GROUP BY t.topic_id,t.label,t.anchor_embedding "
            "ORDER BY t.topic_id", (cluster_run_id,),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError("Cluster run has no current topics")
    topic_ids = {str(topic_id): source_ids for topic_id, _, _, source_ids in rows}
    current_ids = {identifier for values in topic_ids.values() for identifier in values}
    outside = sorted(current_ids - allowed_ids)
    if outside:
        raise ValueError(
            f"Current topic corpus is not contained in the background target pack: {outside[:5]}"
        )
    current = {str(topic_id): parse_vector(vector).tolist()
               for topic_id, _, vector, _ in rows}
    assessed = assess_current_anchors(current, anchors)
    labels = {str(topic_id): label for topic_id, label, _, _ in rows}
    topics = [{
        "topic_id": int(key), "label": labels[key],
        "current_documents": len(topic_ids[key]), **assessed[key],
    } for key in sorted(assessed, key=int)]
    report = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "diagnostic_not_applied_to_score",
        "mission_id": mission_id,
        "cluster_run_id": cluster_run_id,
        "collection_batch_id": batch_id,
        "period": {
            "current_from_inclusive": period_start.isoformat(),
            "current_to_exclusive": period_end.isoformat(),
            "origin": period_origin,
            "historical_before_exclusive": cutoff.isoformat(),
        },
        "provenance": {
            "embedding_model": model,
            "background_manifest_payload_sha256": background_manifest["payload_sha256"],
            "cache_manifest_sha256": cache_manifest["payload_sha256"],
            "target_pack_manifest_sha256": packs_manifest["payload_sha256"],
            "target_pack_sha256": target["sha256"],
            "arxiv_inventory_sha256": source_mode["upstream_inventory_sha256"],
            "current_topics_payload_sha256": _stable_hash([
                [topic_id, label, vector, source_ids]
                for topic_id, label, vector, source_ids in rows
            ]),
            "current_membership_subset_of_target_pack": True,
            "historical_relevance_plan_sha256": background_manifest["source"].get(
                "relevance_plan_sha256"
            ),
        },
        "counts": {
            "current_topics": len(topics),
            "current_topic_documents": len(current_ids),
            "background_documents": background_manifest["counts"]["embedded_eligible"],
            "background_topic_anchors": len(anchors),
        },
        "topics": topics,
        "interpretation": {
            "novelty_compares_current_topic_to_nearest_pre_period_topic": True,
            "percentile_peer_scope": "current_topics_in_same_cluster_run",
            "changes_existing_candidate_score": False,
            "proves_historical_detection": False,
            "historical_text_versions_reconstructed": False,
            "target_pack_membership_is_signal_label": False,
            "controlled_historical_relevance_predicate_reapplied": bool(
                background_manifest["interpretation"].get(
                    "controlled_relevance_predicate_reapplied"
                )
            ),
        },
        "limitations": [
            "Фон построен по текущим версиям метаданных исторических публикаций, а не по восстановленным текстам на дату.",
            "Это проверка новизны текущих тем относительно прошлого; она не доказывает, что система обнаружила бы тему исторически.",
            "Диагностика не изменяет сохранённые карточки и должна быть отдельно встроена в новый score-прогон после проверки.",
        ],
    }
    report["payload_sha256"] = payload_hash(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--packs-dir", type=Path, required=True)
    parser.add_argument("--background-dir", type=Path, required=True)
    parser.add_argument("--cluster-run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = diagnose(
        args.cache_dir, args.packs_dir, args.background_dir,
        args.cluster_run_id, args.output,
    )
    print(json.dumps({
        "version": report["version"], "status": report["status"],
        "counts": report["counts"], "topics": report["topics"],
        "payload_sha256": report["payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
