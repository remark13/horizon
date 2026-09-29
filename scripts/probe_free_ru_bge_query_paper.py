"""Pinned local BGE query-to-paper diagnostic on the frozen 45-paper packet."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from scripts.check_short_application_metadata_review import check


ROOT = Path(__file__).resolve().parents[1]
VERSION = "free-ru-bge-query-paper-probe-v1"


def score_local_pairs(model_dir: Path, pairs: list[tuple[str, str]], *,
                      batch_size: int = 4, max_length: int = 320) -> tuple[list[float], float]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_dir, local_files_only=True, use_safetensors=True).eval()
    scores = []
    started = perf_counter()
    with torch.inference_mode():
        for index in range(0, len(pairs), batch_size):
            batch = pairs[index:index + batch_size]
            encoded = tokenizer(batch, padding=True, truncation=True,
                                max_length=max_length, return_tensors="pt")
            scores.extend(model(**encoded).logits.reshape(-1).float().tolist())
    return scores, round(perf_counter() - started, 3)


def rank_diagnostic(records: list[dict], scores: list[float], review: dict) -> dict:
    if len(records) != len(scores):
        raise ValueError("Model score count does not match frozen records")
    positives = {row["case_id"]: set(row["full_qualifiers_observed_ranks"])
                 for row in review["cases"]}
    by_case = {}
    for record, score in zip(records, scores):
        case = record["case_id"]
        by_case.setdefault(case, []).append({
            "retrieval_rank": record["retrieval_rank"],
            "openalex_id": record["openalex_id"], "score": float(score),
            "developer_metadata_observed": record["retrieval_rank"] in positives[case]})
    for case, rows in by_case.items():
        rows.sort(key=lambda row: (-row["score"], row["retrieval_rank"]))
        for rank, row in enumerate(rows, 1):
            row["reranked_position"] = rank
    uav = {row["openalex_id"].rsplit("/", 1)[-1]: row for row in by_case["national-area-039"]}
    direct = uav["W4387166575"]
    collision = uav["W4212992056"]
    return {"cases": by_case,
            "predeclared_relation_gate_passed": collision["score"] < direct["score"],
            "relation_control": {"topical_review_score": direct["score"],
                                 "land_vehicle_collision_score": collision["score"],
                                 "topical_review_rank": direct["reranked_position"],
                                 "land_vehicle_collision_rank": collision["reranked_position"]}}


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION or config.get("production_use") is not False
            or config.get("input_policy") != "original_RU_query_vs_OpenAlex_title_plus_abstract"
            or config.get("max_pairs") != 45 or config.get("batch_size") != 4
            or config.get("max_length") != 320):
        raise ValueError("Unexpected probe protocol")
    packet_path = (ROOT / config["packet"]).resolve()
    review_path = (ROOT / config["developer_review"]).resolve()
    model_dir = (ROOT / config["local_model_dir"]).resolve()
    if any(not path.is_relative_to(ROOT) for path in (packet_path, review_path, model_dir)):
        raise ValueError("Probe input outside project")
    if (sha256_file(packet_path) != config["packet_sha256"]
            or sha256_file(review_path) != config["developer_review_sha256"]):
        raise ValueError("Frozen review inputs changed")
    check(packet_path, review_path)
    if not (model_dir / "model.safetensors").is_file():
        raise ValueError("Pinned local BGE weights unavailable")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    records = packet["records"]
    if len(records) != 45:
        raise ValueError("Unexpected frozen pair count")
    pairs = [(record["query_ru"], record["title"] + ". " + (record["abstract"] or ""))
             for record in records]
    scores, seconds = score_local_pairs(model_dir, pairs,
                                       batch_size=config["batch_size"],
                                       max_length=config["max_length"])
    result = rank_diagnostic(records, scores, review)
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "packet_sha256": sha256_file(packet_path),
            "review_sha256": sha256_file(review_path),
            "model_repo": config["model_repo"],
            "model_revision": config["model_revision"],
            "model_safetensors_sha256": sha256_file(model_dir / "model.safetensors"),
            "inference_seconds_excluding_model_load": seconds,
            "pair_count": len(pairs), "diagnostic": result,
            "production_changed": False, "weak_signal_quality_measured": False,
            "limitations": config["limitations"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe result is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"seconds": result["inference_seconds_excluding_model_load"],
                      "relation_gate": result["diagnostic"]["predeclared_relation_gate_passed"]}))


if __name__ == "__main__":
    main()
