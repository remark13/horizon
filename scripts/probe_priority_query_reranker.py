"""Offline, frozen comparison of query-document reranking on developer labels."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from math import log2
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
INPUTS_V2 = {
    "packet": ("evaluation/priority-arxiv-pilot-relevance-v2.packet.json",
               "c10787ce5fcf147ba8de3caef3dae89353e029b1fc475e9681aa6b45abeb4e82"),
    "review": ("evaluation/priority-arxiv-pilot-developer-review-v2.json",
               "f7d2a2854082a0a41ff463ce41746741baac10ef1a3cc476c0f5425aefe7c1e3"),
    "baseline": ("outputs/priority-arxiv-pilot-semantic-diagnostic-v2.json",
                 "7af19524d0ab9fc580f8249ee2c8a86f8bdb9dbfb6807470ea1b5be66c88a524"),
    "corpus_manifest": ("data/processed/priority-arxiv-pilot-corpus-v1/manifest.json",
                        "afe9f332ddd0be2f709e60978e686c4dbbb45fddcdc11796aeaeb24f282bb2b2"),
}
INPUTS_V1 = {
    "packet": ("evaluation/priority-arxiv-pilot-relevance-v1.packet.json",
               "f235e917eba99cc848ce333d48aba4e782a8624baa48db6fcabbe02d9460b30d"),
    "review": ("evaluation/priority-arxiv-pilot-developer-review-v1.json",
               "b0f34f2a3c00fe2842384859d78331ff86ce1da1c38bcf5eaaf2bc0bc35ce165"),
    "baseline": ("outputs/priority-arxiv-pilot-semantic-diagnostic-v1.json",
                 "b5ec9e15c4c044c6510e8a4d51e27ddb080e33f96e9d890dbb80fb926c76fc52"),
    "corpus_manifest": INPUTS_V2["corpus_manifest"],
}
MODEL_DIR = ROOT / "data/models/bge-reranker-v2-m3-953dc6f"
LABEL_VALUE = {"no": 0, "partial": 1, "yes": 2}


def read_frozen(relative: str, expected: str) -> dict:
    path = ROOT / relative
    if sha256_file(path) != expected:
        raise ValueError(f"Frozen input changed: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def pairwise(rows: list[dict], score_key: str) -> dict:
    counts = {"yes_over_no": [0, 0], "yes_over_partial": [0, 0],
              "partial_over_no": [0, 0]}
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            if left["topic"] != right["topic"] or left["label"] == right["label"]:
                continue
            better, worse = sorted((left, right), key=lambda row: LABEL_VALUE[row["label"]],
                                   reverse=True)
            key = f"{better['label']}_over_{worse['label']}"
            counts[key][1] += 1
            counts[key][0] += better[score_key] > worse[score_key]
    return {key: {"correct": value[0], "pairs": value[1]}
            for key, value in counts.items()}


def ndcg_at_k(rows: list[dict], score_key: str, k: int) -> float | None:
    if len(rows) < 2 or len({row["label"] for row in rows}) < 2:
        return None

    def dcg(ordered: list[dict]) -> float:
        return sum((2 ** LABEL_VALUE[row["label"]] - 1) / log2(rank + 2)
                   for rank, row in enumerate(ordered[:k]))

    ideal = dcg(sorted(rows, key=lambda row: LABEL_VALUE[row["label"]], reverse=True))
    return dcg(sorted(rows, key=lambda row: row[score_key], reverse=True)) / ideal


def run(packet_version: str) -> dict:
    import pyarrow.parquet as pq
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    inputs_map = INPUTS_V1 if packet_version == "v1" else INPUTS_V2
    expected_count = 67 if packet_version == "v1" else 41
    packet = read_frozen(*inputs_map["packet"])
    review = read_frozen(*inputs_map["review"])
    baseline = read_frozen(*inputs_map["baseline"])
    read_frozen(*inputs_map["corpus_manifest"])
    if review["packet_sha256"] != inputs_map["packet"][1] or len(packet["items"]) != expected_count:
        raise ValueError("Unexpected packet or review")
    labels = {row[0]: row[1] for row in review["labels"]}
    old_scores = {row["item_id"]: row["cosine"] for row in baseline["items"]}
    packet_ids = {item["item_id"] for item in packet["items"]}
    if (len(labels) != expected_count or len(old_scores) != expected_count or
            packet_ids != set(labels) or packet_ids != set(old_scores)):
        raise ValueError("Benchmark assignments do not match")
    corpus = pq.read_table(ROOT / "data/processed/priority-arxiv-pilot-corpus-v1/documents.parquet",
                           columns=["arxiv_id", "title_current", "abstract_current"])
    abstracts = {row["arxiv_id"]: row for row in corpus.to_pylist()}
    if not (MODEL_DIR / "model.safetensors").is_file():
        raise FileNotFoundError(MODEL_DIR)
    rows = []
    inputs = []
    for item in packet["items"]:
        doc = item["document"]
        record = abstracts[doc["arxiv_id"]]
        if record["title_current"].strip() != doc["title"].strip():
            raise ValueError(f"Document title changed: {doc['arxiv_id']}")
        label = labels[item["item_id"]]
        if label not in LABEL_VALUE:
            raise ValueError("Unrecognized label")
        rows.append({"item_id": item["item_id"], "topic": item["target_topic"],
                     "arxiv_id": doc["arxiv_id"], "label": label,
                     "old_score": old_scores[item["item_id"]]})
        inputs.append((item["target_topic"],
                       f"{record['title_current']}. {record['abstract_current']}"))
    torch.set_num_threads(4)
    load_started = perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_DIR, local_files_only=True, use_safetensors=True).eval()
    load_seconds = round(perf_counter() - load_started, 3)
    scores = []
    infer_started = perf_counter()
    with torch.inference_mode():
        for index in range(0, len(inputs), 4):
            encoded = tokenizer(inputs[index:index + 4], padding=True,
                                truncation=True, max_length=320, return_tensors="pt")
            scores.extend(model(**encoded).logits.reshape(-1).float().tolist())
    infer_seconds = round(perf_counter() - infer_started, 3)
    for row, score in zip(rows, scores, strict=True):
        row["new_score"] = score
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["topic"]].append(row)
    topics = []
    for topic, items in groups.items():
        topics.append({"topic": topic, "n": len(items),
                       "labels": {label: sum(item["label"] == label for item in items)
                                  for label in LABEL_VALUE},
                       "old_ndcg_at_5": ndcg_at_k(items, "old_score", 5),
                       "new_ndcg_at_5": ndcg_at_k(items, "new_score", 5)})
    comparable = [item for item in topics if item["old_ndcg_at_5"] is not None]
    return {"version": "priority-query-reranker-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "packet_version": packet_version,
            "input_sha256": {key: digest for key, (_, digest) in inputs_map.items()},
            "model_repo": "BAAI/bge-reranker-v2-m3",
            "model_revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
            "model_safetensors_sha256": sha256_file(MODEL_DIR / "model.safetensors"),
            "query_policy": "raw_Russian_target_topic_vs_current_arxiv_title_plus_abstract",
            "baseline": "saved_BGE-M3_cosine_on_same_items",
            "max_length_tokens": 320, "batch_size": 4,
            "model_load_seconds": load_seconds, "model_inference_seconds": infer_seconds,
            "items": rows, "topic_results": topics,
            "old_pairwise": pairwise(rows, "old_score"),
            "new_pairwise": pairwise(rows, "new_score"),
            "old_macro_ndcg_at_5": sum(row["old_ndcg_at_5"] for row in comparable) / len(comparable),
            "new_macro_ndcg_at_5": sum(row["new_ndcg_at_5"] for row in comparable) / len(comparable),
            "evaluated_topics": len(comparable),
            "limitations": ["Developer labels, not independent expert validation",
                            "Selected retrieved documents, not free-query discovery",
                            "Article relevance, not weak-signal validity",
                            "No model threshold, score calibration or production switch"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-version", choices=["v1", "v2"], default="v2")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = run(args.packet_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
