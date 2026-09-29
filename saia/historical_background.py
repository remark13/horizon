"""Immutable, time-bounded semantic background for current weak-signal topics.

The thematic target pack is only a broad retrieval superset.  This module
turns its publications strictly before a declared cutoff into SPECTER2 topic
anchors.  Pack membership itself is never interpreted as a signal or label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from saia import methodology
from saia.arxiv_metadata import first_submission, matches_controlled_plan
from saia.candidates import percentile
from saia.cluster import cluster_window, cosine_matrix
from saia.embed import (
    SPECTER2_ADAPTER_REVISION,
    SPECTER2_BASE_REVISION,
    Specter2Embedder,
    validate_vector_batch,
)
from saia.package_observations import payload_hash
from saia.specter2_smoke import _adapter_effect, peak_rss_bytes
from saia.thematic_arxiv_cache import load_cache, load_target_packs


VERSION = "historical-semantic-background-0.4.42-r3"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _target(packs: dict, key: str) -> dict:
    matches = [item for item in packs["targets"] if item["target_key"] == key]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous thematic target pack: {key}")
    return matches[0]


def historical_rows(pack_path: Path, cutoff: date, *, max_records: int,
                    plan: dict) -> tuple[list[dict], dict]:
    if type(max_records) is not int or not 1 <= max_records <= 100_000:
        raise ValueError("max_records must be an integer between 1 and 100000")
    if not list(plan.get("included_terms") or []):
        raise ValueError(
            "A non-empty controlled relevance plan is required; a target pack is only a superset"
        )
    rows: list[dict] = []
    seen: set[str] = set()
    counts = Counter()
    for batch in pq.ParquetFile(pack_path).iter_batches(batch_size=8192):
        for row in pa.Table.from_batches([batch]).to_pylist():
            counts["pack_rows"] += 1
            identifier = str(row.get("id") or "").strip()
            if not identifier or identifier in seen:
                raise ValueError("Target pack contains an empty or duplicate arXiv id")
            seen.add(identifier)
            published = date.fromisoformat(first_submission(row.get("versions")))
            if published >= cutoff:
                counts["at_or_after_cutoff"] += 1
                continue
            counts["before_cutoff"] += 1
            if not matches_controlled_plan(row, plan):
                counts["excluded_by_relevance_plan"] += 1
                continue
            counts["pre_cutoff_relevance_matches"] += 1
            title = str(row.get("title") or "").strip()
            abstract = str(row.get("abstract") or "").strip()
            if not title or not abstract:
                counts["excluded_missing_title_or_abstract"] += 1
                continue
            rows.append({
                "arxiv_id": identifier,
                "published": published,
                "title": title,
                "abstract": abstract,
            })
            if len(rows) > max_records:
                raise ValueError(
                    "Historical background exceeds max_records; it must be narrowed "
                    "or processed completely, never silently truncated"
                )
    rows.sort(key=lambda row: (row["published"], row["arxiv_id"]))
    counts["embedded_eligible"] = len(rows)
    if not rows:
        raise ValueError("No pre-cutoff title/abstract publications in target pack")
    return rows, dict(counts)


def _unit_rows(vectors: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or not matrix.shape[0] or not np.isfinite(matrix).all():
        raise ValueError("Background vectors must form a finite non-empty matrix")
    norms = np.linalg.norm(matrix, axis=1)
    if (norms == 0).any() or not np.isfinite(norms).all():
        raise ValueError("Background vectors contain zero or invalid rows")
    return matrix / norms[:, None]


def topic_anchors(rows: list[dict], vectors: list[list[float]], *,
                  min_topic_size: int, seed: int, umap: dict,
                  hdbscan: dict, small_window_similarity_threshold: float) -> tuple[list[dict], list[int]]:
    matrix = _unit_rows(vectors)
    texts = [row["title"] + " " + row["abstract"] for row in rows]
    labels, terms = cluster_window(
        texts, matrix, min_topic_size, seed, umap, hdbscan,
        small_window_similarity_threshold,
    )
    anchors = []
    for label in sorted(int(value) for value in set(labels.tolist()) if value != -1):
        indexes = np.flatnonzero(labels == label)
        centroid = matrix[indexes].mean(axis=0)
        centroid /= np.linalg.norm(centroid)
        dates = [rows[index]["published"] for index in indexes]
        anchors.append({
            "background_topic": label,
            "documents": len(indexes),
            "first_publication": min(dates),
            "last_publication": max(dates),
            "top_terms": terms.get(label, []),
            "anchor": centroid.astype(np.float32).tolist(),
        })
    return anchors, labels.astype(int).tolist()


def assess_current_anchors(current: dict[str, list[float]], background: list[dict],
                           *, min_peers: int = 5,
                           rank_method: str = "midrank") -> dict[str, dict]:
    if not background:
        raise ValueError("Historical background has no topic anchors")
    keys = sorted(current)
    current_matrix = _unit_rows([current[key] for key in keys])
    background_matrix = _unit_rows([item["anchor"] for item in background])
    similarities = cosine_matrix(current_matrix, background_matrix)
    raw = {key: 1.0 - float(similarities[index].max())
           for index, key in enumerate(keys)}
    population = list(raw.values())
    result = {}
    for index, key in enumerate(keys):
        nearest = int(np.argmax(similarities[index]))
        item = background[nearest]
        result[key] = {
            "novelty_raw": raw[key],
            "novelty_percentile": percentile(
                raw[key], population, min_peers, rank_method
            ),
            "novelty_availability": (
                "available" if len(population) >= min_peers
                else "insufficient_current_peer_topics"
            ),
            "nearest_background_topic": item["background_topic"],
            "nearest_background_similarity": float(similarities[index, nearest]),
            "nearest_background_terms": item["top_terms"],
            "historical_anchor_topics": len(background),
            "current_peer_topics": len(population),
        }
    return result


def build(cache_dir: Path, packs_dir: Path, target_key: str, cutoff: date,
          output: Path, *, max_records: int = 2000, batch_size: int = 8,
          max_rss_gib: float = 8.0, included_terms: list[str],
          exclusions: list[str] | None = None, embedder=None) -> dict:
    if output.exists():
        raise FileExistsError("Historical background output already exists")
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if not math.isfinite(max_rss_gib) or max_rss_gib <= 0:
        raise ValueError("max_rss_gib must be positive")
    if embedder is None and (
        os.environ.get("HF_HUB_OFFLINE") != "1"
        or os.environ.get("TRANSFORMERS_OFFLINE") != "1"
    ):
        raise ValueError("Set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1")

    cache_manifest, _ = load_cache(cache_dir)
    packs = load_target_packs(packs_dir, cache_manifest["payload_sha256"])
    target = _target(packs, target_key)
    pack_path = packs_dir / target["file"]
    plan = {
        "included_terms": [str(value).strip() for value in included_terms if str(value).strip()],
        "exclusions": [str(value).strip() for value in (exclusions or []) if str(value).strip()],
    }
    rows, counts = historical_rows(
        pack_path, cutoff, max_records=max_records, plan=plan
    )
    cfg = methodology.load_default().clustering
    min_topic_size = int(cfg["scales"]["micro"]["min_topic_size"])

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=output.name + ".tmp-", dir=output.parent))
    try:
        rss_before = peak_rss_bytes()
        started = time.perf_counter()
        model = embedder or Specter2Embedder()
        model_load_seconds = time.perf_counter() - started
        adapter_probe = (
            _adapter_effect(model, rows[0]["title"] + "\n\n" + rows[0]["abstract"])
            if embedder is None else {"skipped_for_injected_test_embedder": True}
        )
        vectors: list[list[float]] = []
        dimension = None
        embedding_started = time.perf_counter()
        for offset in range(0, len(rows), batch_size):
            chunk = rows[offset:offset + batch_size]
            values = model.embed([
                row["title"] + "\n\n" + row["abstract"] for row in chunk
            ])
            dimension = validate_vector_batch(values, len(chunk), dimension)
            vectors.extend(values)
        embedding_seconds = time.perf_counter() - embedding_started
        anchors, labels = topic_anchors(
            rows, vectors, min_topic_size=min_topic_size,
            seed=int(cfg["seed"]), umap=dict(cfg["umap"]),
            hdbscan=dict(cfg["hdbscan"]),
            small_window_similarity_threshold=float(
                cfg.get("graph", {}).get("similarity_threshold", 0.35)
            ),
        )
        if not anchors:
            raise ValueError("Historical clustering produced no topic anchors")

        doc_table = pa.table({
            "arxiv_id": [row["arxiv_id"] for row in rows],
            "published": pa.array([row["published"] for row in rows], type=pa.date32()),
            "title": [row["title"] for row in rows],
            "abstract": [row["abstract"] for row in rows],
            "background_topic": labels,
            "embedding": pa.array(vectors, type=pa.list_(pa.float32(), dimension)),
        })
        anchor_table = pa.table({
            "background_topic": [item["background_topic"] for item in anchors],
            "documents": [item["documents"] for item in anchors],
            "first_publication": pa.array(
                [item["first_publication"] for item in anchors], type=pa.date32()
            ),
            "last_publication": pa.array(
                [item["last_publication"] for item in anchors], type=pa.date32()
            ),
            "top_terms": [item["top_terms"] for item in anchors],
            "anchor": pa.array(
                [item["anchor"] for item in anchors],
                type=pa.list_(pa.float32(), dimension),
            ),
        })
        pq.write_table(doc_table, temporary / "historical_documents.parquet", compression="zstd")
        pq.write_table(anchor_table, temporary / "background_anchors.parquet", compression="zstd")
        rss_peak = peak_rss_bytes()
        max_rss_bytes = int(max_rss_gib * 1024 ** 3)
        manifest = {
            "version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "passed" if rss_peak <= max_rss_bytes else "rss_limit_exceeded",
            "source": {
                "cache_manifest_sha256": cache_manifest["payload_sha256"],
                "target_pack_manifest_sha256": packs["payload_sha256"],
                "target_key": target_key,
                "target_pack_file": target["file"],
                "target_pack_sha256": target["sha256"],
                "target_pack_rows": target["rows"],
                "cutoff_exclusive": cutoff.isoformat(),
                "relevance_plan": plan,
                "relevance_plan_sha256": payload_hash(plan),
            },
            "counts": {
                **counts,
                "background_topics": len(anchors),
                "clustered_documents": sum(item["documents"] for item in anchors),
                "noise_documents": sum(label == -1 for label in labels),
            },
            "model": {
                "name": model.name,
                "base_revision": SPECTER2_BASE_REVISION,
                "adapter_revision": SPECTER2_ADAPTER_REVISION,
                "dimension": dimension,
                "batch_size": batch_size,
                "adapter_name": getattr(model, "adapter_name", None),
                "active_adapters": getattr(model, "active_adapters", None),
                "adapter_effect_probe": adapter_probe,
            },
            "clustering": {
                "backend": "bertopic_on_pre_cutoff_background_only",
                "min_topic_size": min_topic_size,
                "seed": int(cfg["seed"]),
                "umap": cfg["umap"],
                "hdbscan": cfg["hdbscan"],
            },
            "runtime": {
                "model_load_seconds": model_load_seconds,
                "embedding_seconds": embedding_seconds,
                "peak_rss_bytes": rss_peak,
                "rss_before_model_bytes": rss_before,
                "max_rss_bytes": max_rss_bytes,
                "within_rss_limit": rss_peak <= max_rss_bytes,
                "host_free_bytes_after": shutil.disk_usage(output.parent).free,
            },
            "files": {},
            "interpretation": {
                "target_pack_membership_is_signal_label": False,
                "target_pack_membership_is_relevance_label": False,
                "controlled_relevance_predicate_reapplied": True,
                "background_is_strictly_pre_cutoff": True,
                "current_topics_used_to_build_background": False,
                "historical_text_versions_reconstructed": False,
                "safe_for_current_reconstruction_novelty": True,
                "proves_historical_detection": False,
            },
        }
        for name in ("historical_documents.parquet", "background_anchors.parquet"):
            path = temporary / name
            manifest["files"][name] = {
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        manifest["payload_sha256"] = payload_hash(manifest)
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, output)
        return manifest
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load(output: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    claimed = manifest.get("payload_sha256")
    if claimed != payload_hash({key: value for key, value in manifest.items()
                               if key != "payload_sha256"}):
        raise ValueError("Historical background manifest hash mismatch")
    if manifest.get("version") != VERSION or manifest.get("status") != "passed":
        raise ValueError("Historical background is unsupported or did not pass")
    for name, metadata in manifest["files"].items():
        path = output / name
        if path.stat().st_size != metadata["bytes"] or sha256_file(path) != metadata["sha256"]:
            raise ValueError(f"Historical background file identity mismatch: {name}")
    table = pq.read_table(output / "background_anchors.parquet")
    anchors = []
    for row in table.to_pylist():
        anchors.append({
            **row,
            "first_publication": row["first_publication"].isoformat(),
            "last_publication": row["last_publication"].isoformat(),
        })
    return manifest, anchors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--packs-dir", type=Path, required=True)
    parser.add_argument("--target-key", required=True)
    parser.add_argument("--cutoff", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-rss-gib", type=float, default=8.0)
    parser.add_argument("--include-term", action="append", required=True)
    parser.add_argument("--exclude-term", action="append", default=[])
    args = parser.parse_args(argv)
    result = build(
        args.cache_dir, args.packs_dir, args.target_key, args.cutoff, args.output,
        max_records=args.max_records, batch_size=args.batch_size,
        max_rss_gib=args.max_rss_gib, included_terms=args.include_term,
        exclusions=args.exclude_term,
    )
    print(json.dumps({
        "version": result["version"], "status": result["status"],
        "counts": result["counts"], "model": result["model"],
        "runtime": result["runtime"], "payload_sha256": result["payload_sha256"],
    }, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
