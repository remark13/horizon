"""Frozen BGE-M3 comparison of own-claim text against BAS task pairs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import numpy as np

from saia.composition_features import fit_univariate
from saia.controlled_collection import sha256_file
from saia.embed import OllamaEmbedder
from saia.query_translation_pilot import _model_digest
from scripts.probe_bas_claim_pair_similarity import _claim_texts, _frozen_json
from scripts.probe_bas_task_pair_similarity import _balanced, _source, _works, _validated_pairs


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-claim-bge-pair-v1"


def _unit_vectors(values: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float64)
    if (matrix.ndim != 2 or not np.isfinite(matrix).all()
            or np.any(np.linalg.norm(matrix, axis=1) == 0)):
        raise ValueError("Invalid embedding batch")
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def evaluate(pair_config: dict, source: dict, claim_texts: dict[int, str],
             baseline: dict, embedder) -> dict:
    works, card_of = _works(pair_config, source)
    pairs = _validated_pairs(pair_config, works, card_of)
    if set(claim_texts) != set(works) or baseline["pair_count"] != len(pairs):
        raise ValueError("Pair universe or claim inputs differ")
    ids = sorted(works)
    nonempty = [identifier for identifier in ids if claim_texts[identifier]]
    claim_batch = _unit_vectors(embedder.embed([claim_texts[i] for i in nonempty]))
    by_id = {identifier: claim_batch[index] for index, identifier in enumerate(nonempty)}
    title_batch = _unit_vectors(embedder.embed([
        works[i]["title"] + ". " + claim_texts[i] for i in ids]))
    title_by_id = {identifier: title_batch[index] for index, identifier in enumerate(ids)}
    rows = []
    for pair in pairs:
        a, b = pair["a"], pair["b"]
        rows.append({"a": a, "b": b, "partition": pair["partition"],
                     "target": pair["same_task"],
                     "claim_bge_cosine": round(float(np.dot(by_id[a], by_id[b])), 6)
                     if a in by_id and b in by_id else 0.0,
                     "title_plus_claim_bge_cosine": round(float(np.dot(
                         title_by_id[a], title_by_id[b])), 6)})
    holdout = {}
    fits = {}
    for feature in ("claim_bge_cosine", "title_plus_claim_bge_cosine"):
        fit = fit_univariate([row for row in rows if row["partition"] == "development"],
                             feature, "higher_supports_coherent_line", 5)
        fits[feature] = fit
        predicted = [{**row, "predicted": row[feature] >= fit["threshold"]}
                     for row in rows if row["partition"] == "holdout"]
        holdout[feature] = {"balanced_accuracy": _balanced(predicted),
                            "true_same_retained": sum(row["predicted"] and row["target"]
                                                      for row in predicted),
                            "false_same_on_different": sum(row["predicted"] and not row["target"]
                                                           for row in predicted),
                            "threshold_from_development": fit["threshold"]}
    digest = hashlib.sha256(claim_batch.astype("<f8").tobytes() +
                            title_batch.astype("<f8").tobytes()).hexdigest()
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "work_count": len(works), "works_without_claim_text": len(ids) - len(nonempty),
            "pair_count": len(rows), "embedding_sha256": digest,
            "development_fits": fits, "holdout_diagnostics": holdout,
            "baseline": {"feature": baseline["selected_feature"],
                         "holdout_balanced_accuracy": baseline["holdout_balanced_accuracy"],
                         "holdout_false_same_on_different": baseline["holdout_false_same_on_different"]},
            "rows": rows, "production_changed": False,
            "independent_accuracy_measured": False,
            "weak_signal_accuracy_measured": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if (cfg.get("version") != VERSION or cfg.get("production_use") is not False
            or cfg.get("api_root") != "http://127.0.0.1:11434"):
        raise ValueError("Invalid local diagnostic protocol")
    if _model_digest(cfg["api_root"], cfg["model"]) != cfg["model_digest"]:
        raise ValueError("Pinned local embedding model changed")
    pair_config = _frozen_json(cfg, "pair_config")
    baseline = _frozen_json(cfg, "baseline_result")
    source = _source(pair_config)
    works, _ = _works(pair_config, source)
    claim_texts, outside_parent = _claim_texts(cfg, works)
    result = evaluate(pair_config, source, claim_texts, baseline,
                      OllamaEmbedder(model=cfg["model"], url=cfg["api_root"]))
    result.update({"config_sha256": sha256_file(args.config),
                   "model": cfg["model"], "model_digest": cfg["model_digest"],
                   "outside_claim_index_parent_work_ids": outside_parent,
                   "limitations": cfg["limitations"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["holdout_diagnostics"]), flush=True)


if __name__ == "__main__":
    main()
