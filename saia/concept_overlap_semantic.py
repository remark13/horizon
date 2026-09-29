"""Exploratory RU-query reranking of a frozen, developer-reviewed arXiv packet.

This is a diagnostic on a selected sample, not a production ranker or an
estimate of retrieval precision. A model threshold must not be fitted here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.embed import OllamaEmbedder
from saia.priority_catalog_vectors import MODEL
from saia.priority_relevance_semantic import _batch_embed, _cosine
from saia.query_translation_pilot import LOCAL_API, _model_digest


VERSION = "ru-concept-overlap-semantic-diagnostic-v1"
RANK = {"strict": 2, "partial": 1, "off_topic": 0}


def _pairwise(rows: list[dict]) -> dict:
    counts = Counter()
    by_case = defaultdict(list)
    for row in rows:
        by_case[row["case_id"]].append(row)
    for items in by_case.values():
        for better in items:
            for worse in items:
                if RANK[better["label"]] <= RANK[worse["label"]]:
                    continue
                key = better["label"] + "_over_" + worse["label"]
                counts[key + "_pairs"] += 1
                if better["cosine"] > worse["cosine"]:
                    counts[key + "_correct"] += 1
                elif better["cosine"] == worse["cosine"]:
                    counts[key + "_tied"] += 1
    return {"counts": dict(sorted(counts.items())),
            "only_within_same_case": True, "no_threshold_or_calibration": True}


def run(*, packet_path: Path, review_path: Path, proposals_path: Path,
        embedder: OllamaEmbedder | None = None, model_digest: str | None = None,
        batch_size: int = 16) -> dict:
    if not 1 <= batch_size <= 32:
        raise ValueError("Embedding batch size must be 1–32")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    if (packet.get("version") != "ru-concept-overlap-review-packet-v1"
            or review.get("version") != "ru-concept-overlap-developer-review-v1"
            or review.get("policy", {}).get("not_independent_expert_review") is not True
            or proposals.get("version") != "ru-concept-plan-diagnostic-v2"
            or packet.get("proposals_sha256") != sha256_file(proposals_path)):
        raise ValueError("Diagnostic inputs are not the frozen compatible versions")
    cases = {row["case_id"]: row["query_ru"] for row in proposals["rows"]}
    if len(cases) != len(proposals["rows"]) or any(
            case_id not in cases for case_id in packet["case_ids"]):
        raise ValueError("Missing or duplicate query case")
    labels = {(row["case_id"], row["arxiv_id"]): row["label"]
              for row in review["rows"]}
    keys = [(row["case_id"], row["arxiv_id"]) for row in packet["rows"]]
    if (len(labels) != len(review["rows"]) or len(keys) != len(set(keys))
            or set(keys) != set(labels) or any(label not in RANK for label in labels.values())):
        raise ValueError("Review labels do not match every packet row")
    ordered_cases = packet["case_ids"]
    texts = [cases[case_id] for case_id in ordered_cases] + [
        row["title"] + ". " + row["abstract"] for row in packet["rows"]]
    embedder = embedder or OllamaEmbedder(model=MODEL, url=LOCAL_API)
    digest = model_digest or _model_digest(LOCAL_API, MODEL)
    if not digest:
        raise ValueError("Local model digest is missing")
    started = perf_counter()
    vectors = _batch_embed(embedder, texts, batch_size)
    duration = perf_counter() - started
    if len(vectors) != len(texts):
        raise ValueError("Embedding count mismatch")
    query_vectors = dict(zip(ordered_cases, vectors[:len(ordered_cases)]))
    scored = []
    for row, vector in zip(packet["rows"], vectors[len(ordered_cases):]):
        key = (row["case_id"], row["arxiv_id"])
        scored.append({"case_id": key[0], "arxiv_id": key[1],
                       "label": labels[key],
                       "matched_group_count": row["matched_group_count"],
                       "cosine": _cosine(query_vectors[key[0]], vector)})
    top = []
    for case_id in ordered_cases:
        ranked = sorted((row for row in scored if row["case_id"] == case_id),
                        key=lambda row: (-row["cosine"], row["arxiv_id"]))
        top.append({"case_id": case_id, "sample_size": len(ranked),
                    "sample_top_three": [
                        {"arxiv_id": row["arxiv_id"], "label": row["label"],
                         "cosine": row["cosine"]} for row in ranked[:3]]})
    return {"version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "packet_sha256": sha256_file(packet_path),
            "review_sha256": sha256_file(review_path),
            "proposals_sha256": sha256_file(proposals_path),
            "model": MODEL, "model_digest": digest,
            "embedding_dimension": len(vectors[0]),
            "embedded_texts": len(texts), "embedding_seconds": duration,
            "input_policy": "original Russian query vs current arXiv title and abstract",
            "pairwise": _pairwise(scored), "sample_top_three_by_case": top,
            "rows": scored,
            "limits": {"developer_labels_only": True,
                       "selected_sample_not_population_precision": True,
                       "current_snapshot_text_not_historical_text": True,
                       "no_independent_quality_claim": True,
                       "no_production_threshold_selected": True,
                       "not_weak_signal_validity": True}}
