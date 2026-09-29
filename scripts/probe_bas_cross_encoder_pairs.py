"""Pinned BGE reranker relevance diagnostic on frozen BAS research-task pairs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-cross-encoder-pair-pilot-v1"


def bound_json(relative: str, expected_hash: str) -> dict:
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != expected_hash:
        raise ValueError("Frozen input differs")
    return json.loads(path.read_text(encoding="utf-8"))


def fit_threshold(rows: list[dict]) -> dict:
    positives = [row for row in rows if row["same_task"]]
    negatives = [row for row in rows if not row["same_task"]]
    if len(positives) != 5 or len(negatives) != 5:
        raise ValueError("Development requires five examples per class")
    scores = sorted({float(row["score"]) for row in rows})
    candidates = [scores[0] - 1.0, *[(a + b) / 2 for a, b in zip(scores, scores[1:])],
                  scores[-1] + 1.0]
    best = None
    for threshold in candidates:
        true_same = sum(row["score"] >= threshold for row in positives)
        true_different = sum(row["score"] < threshold for row in negatives)
        balanced = (true_same / 5 + true_different / 5) / 2
        value = (balanced, threshold)
        if best is None or value > best:
            best = value
    assert best is not None
    return {"threshold": best[1], "development_balanced_accuracy": best[0]}


def evaluate(config: dict, task_pairs: dict, contrast: dict,
             pair_scores: dict[tuple[int, int], float]) -> dict:
    development = []
    holdout = []
    for pair in task_pairs["pairs"]:
        key = (pair["a"], pair["b"])
        row = {"a": pair["a"], "b": pair["b"],
               "same_task": pair["same_task"], "score": pair_scores[key]}
        (development if pair["partition"] == "development" else holdout).append(row)
    fitted = fit_threshold(development)
    threshold = fitted["threshold"]
    holdout_rows = [{**row, "predicted_same_task": row["score"] >= threshold}
                    for row in holdout]
    contrast_rows = []
    for row in contrast["rows"]:
        if row["contrast_type"] == "parent_family_only":
            continue
        a, b = [work["work_id"] for work in row["works"]]
        contrast_rows.append({"rank": row["rank"], "a": a, "b": b,
                              "score": pair_scores[(a, b)],
                              "false_same": pair_scores[(a, b)] >= threshold})
    true_same = sum(row["predicted_same_task"] for row in holdout_rows
                    if row["same_task"])
    true_different = sum(not row["predicted_same_task"] for row in holdout_rows
                         if not row["same_task"])
    return {"threshold_fit": fitted,
            "holdout_balanced_accuracy": (true_same / 5 + true_different / 5) / 2,
            "holdout_same_retained": true_same,
            "holdout_different_separated": true_different,
            "contrast_different_separated": sum(not row["false_same"]
                                                  for row in contrast_rows),
            "contrast_total": len(contrast_rows),
            "development_pairs": development,
            "holdout_pairs": holdout_rows,
            "contrast_pairs": contrast_rows,
            "production_changed": False,
            "signal_quality_measured": False,
            "limitations": config["limitations"]}


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION or config.get("production_use") is not False
            or config.get("input_policy") !=
            "symmetric_mean_logit_of_title_query_against_other_title_plus_abstract"
            or config.get("threshold_policy") !=
            "fit_on_five_positive_and_five_negative_development_pairs_only; maximize_balanced_accuracy_then_choose_highest_threshold"):
        raise ValueError("Unknown or production-enabled protocol")
    source = bound_json(config["source"], config["source_sha256"])
    task_pairs = bound_json(config["task_pairs"], config["task_pairs_sha256"])
    contrast = bound_json(config["contrast_report"], config["contrast_report_sha256"])
    works = {work["work_id"]: work for card in source["cards"]
             for work in card["works"]}
    pairs = [(row["a"], row["b"]) for row in task_pairs["pairs"]]
    pairs += [tuple(work["work_id"] for work in row["works"])
              for row in contrast["rows"] if row["contrast_type"] != "parent_family_only"]
    if len(pairs) != 34 or any(a not in works or b not in works for a, b in pairs):
        raise ValueError("Unexpected frozen pair population")
    model_dir = (ROOT / config["local_model_dir"]).resolve()
    if not model_dir.is_relative_to(ROOT) or not (model_dir / "model.safetensors").is_file():
        raise ValueError("Pinned local model absent")
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_dir, local_files_only=True, use_safetensors=True).eval()
    model_sha256 = sha256_file(model_dir / "model.safetensors")
    ordered_inputs = []
    for a, b in pairs:
        ordered_inputs.append((works[a]["title"],
                               f"{works[b]['title']}. {works[b]['abstract']}"))
        ordered_inputs.append((works[b]["title"],
                               f"{works[a]['title']}. {works[a]['abstract']}"))
    logits = []
    started = perf_counter()
    with torch.inference_mode():
        for index in range(0, len(ordered_inputs), config["batch_size"]):
            batch = ordered_inputs[index:index + config["batch_size"]]
            encoded = tokenizer(batch, padding=True, truncation=True,
                                max_length=config["max_length"], return_tensors="pt")
            scores = model(**encoded).logits.reshape(-1).float().tolist()
            logits.extend(scores)
    seconds = round(perf_counter() - started, 3)
    if len(logits) != len(pairs) * 2:
        raise ValueError("Missing model scores")
    pair_scores = {(a, b): (logits[2 * index] + logits[2 * index + 1]) / 2
                   for index, (a, b) in enumerate(pairs)}
    result = evaluate(config, task_pairs, contrast, pair_scores)
    result.update({"version": VERSION,
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "config_sha256": sha256_file(config_path),
                   "model_repo": config["model_repo"],
                   "model_revision": config["model_revision"],
                   "model_safetensors_sha256": model_sha256,
                   "model_inference_seconds": seconds,
                   "pair_count": len(pairs), "ordered_model_inputs": len(ordered_inputs)})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Pair report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
