"""Bounded source-grounded object/task/mechanism diagnostic, never production.

The frozen pair labels were already inspected. This can reject a poor idea,
but cannot establish independent accuracy or promote a production threshold.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

import numpy as np

from saia.composition_features import fit_univariate
from saia.controlled_collection import sha256_file
from saia.embed import OllamaEmbedder
from saia.query_translation_pilot import _model_digest, _post
from scripts.probe_bas_claim_pair_similarity import _frozen_json
from scripts.probe_bas_task_pair_similarity import _balanced, _source, _works, _validated_pairs
from scripts.probe_grounded_task_facets_v3 import source_spans


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-structured-line-facets-v1"
FEATURES = ("task_cosine", "min_three_facets", "mean_three_facets")
ROLES = ("primary_result", "evaluation_or_benchmark", "review_or_survey",
         "application_only", "unclear")
FACETS = ("object", "task", "mechanism")


def schema(spans: dict[str, str]) -> dict:
    ids = ["none", *spans]
    properties = {name: {"type": "string"} for name in FACETS}
    properties.update({name + "_id": {"type": "string", "enum": ids}
                       for name in FACETS})
    properties.update({"role": {"type": "string", "enum": list(ROLES)},
                       "role_id": {"type": "string", "enum": ids}})
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": [*FACETS,
                *(name + "_id" for name in FACETS), "role", "role_id"]}


def prompt(spans: dict[str, str]) -> str:
    evidence = "\n".join(f"[{key}] {value}" for key, value in spans.items())
    return (
        "Describe THIS paper's own research contribution, not prior work, "
        "motivation, an evaluation dataset, or a possible future application. "
        "Return THREE distinct short facets in English: object = the thing or "
        "system actually studied (not a branded method, model, benchmark or "
        "paper title); task = the specific outcome or problem addressed; "
        "mechanism = the technical principle used to achieve it, not merely "
        "the name of the proposed system. Do not put a method in task or an "
        "outcome in mechanism. Choose an ABSTRACT sentence ID supporting each "
        "facet. If it is not stated, write 'unknown' and ID 'none'. Role is "
        "primary_result, evaluation_or_benchmark, review_or_survey, "
        "application_only or unclear, with a supporting ID or none. "
        "A sentence about an evaluation on UAV data does not make UAVs the "
        "research object of a general method. Never use only an introductory "
        "sentence as evidence for the paper's contribution. JSON only.\n"
        "Abstract sentences:\n" + evidence
    )


def validate(raw: dict, spans: dict[str, str]) -> dict:
    value = json.loads(raw["response"])
    required = set(schema(spans)["required"])
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Facet fields differ from frozen schema")
    for name in FACETS:
        label, source = value[name], value[name + "_id"]
        if (not isinstance(label, str) or not label.strip()
                or not isinstance(source, str) or source not in {"none", *spans}
                or (label.casefold().strip() == "unknown") != (source == "none")):
            raise ValueError("Facet is empty, ungrounded, or has invalid unknown state")
    if (value["role"] not in ROLES or value["role_id"] not in {"none", *spans}):
        raise ValueError("Role is invalid")
    return {**value, "source_sentences": {
        name: None if value[name + "_id"] == "none" else spans[value[name + "_id"]]
        for name in FACETS},
        "role_source_sentence": None if value["role_id"] == "none"
        else spans[value["role_id"]]}


def unit(vectors: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise ValueError("Invalid facet embeddings")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms <= 0):
        raise ValueError("Empty facet embedding")
    return matrix / norms[:, None]


def evaluate(pair_config: dict, source: dict, rows: list[dict], embedder) -> dict:
    works, card_of = _works(pair_config, source)
    pairs = _validated_pairs(pair_config, works, card_of)
    by_id = {row["work_id"]: row for row in rows}
    if set(by_id) != set(works) or len(by_id) != len(rows):
        raise ValueError("Every source work needs exactly one result")
    texts = []
    positions = {}
    for work_id in sorted(works):
        row = by_id[work_id]
        if row["status"] != "valid_source_ids":
            continue
        for name in FACETS:
            label = row["facets"][name]
            if label == "unknown":
                continue
            positions[work_id, name] = len(texts)
            texts.append(label)
    vectors = unit(embedder.embed(texts)) if texts else np.empty((0, 0))
    pair_rows = []
    for pair in pairs:
        sims = {}
        for name in FACETS:
            left, right = positions.get((pair["a"], name)), positions.get((pair["b"], name))
            sims[name] = round(float(np.dot(vectors[left], vectors[right])), 6) \
                if left is not None and right is not None else None
        all_known = all(value is not None for value in sims.values())
        pair_rows.append({"a": pair["a"], "b": pair["b"],
                          "partition": pair["partition"], "target": pair["same_task"],
                          "facet_cosines": sims,
                          "task_cosine": sims["task"] if sims["task"] is not None else -1.0,
                          "min_three_facets": min(sims.values()) if all_known else -1.0,
                          "mean_three_facets": round(sum(sims.values()) / 3, 6)
                          if all_known else -1.0})
    fits = {}
    holdout = {}
    for feature in FEATURES:
        fit = fit_univariate([row for row in pair_rows if row["partition"] == "development"],
                             feature, "higher_supports_coherent_line", 5)
        fits[feature] = fit
        predicted = [{**row, "predicted": row[feature] >= 0
                      and row[feature] >= fit["threshold"]}
                     for row in pair_rows if row["partition"] == "holdout"]
        holdout[feature] = {"balanced_accuracy": _balanced(predicted),
                            "true_same_retained": sum(row["predicted"] and row["target"]
                                                      for row in predicted),
                            "false_same_on_different": sum(row["predicted"] and not row["target"]
                                                            for row in predicted),
                            "threshold_from_development": fit["threshold"]}
    return {"article_count": len(rows),
            "valid_article_facets": sum(row["status"] == "valid_source_ids" for row in rows),
            "pair_count": len(pair_rows), "development_fits": fits,
            "holdout_diagnostics": holdout, "pair_rows": pair_rows}


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION or config.get("production_use") is not False
            or config.get("api_root") != "http://127.0.0.1:11434"
            or config.get("feature_names") != list(FEATURES)):
        raise ValueError("Unknown or production-enabled structured facet probe")
    if (_model_digest(config["api_root"], config["extractor_model"])
            != config["extractor_digest"]
            or _model_digest(config["api_root"], config["similarity_model"])
            != config["similarity_digest"]):
        raise ValueError("Pinned local model changed or is unavailable")
    pair_config = _frozen_json(config, "pair_config")
    baseline = _frozen_json(config, "baseline_result")
    source = _source(pair_config)
    works, card_of = _works(pair_config, source)
    _validated_pairs(pair_config, works, card_of)
    rows = []
    for work_id, work in sorted(works.items()):
        spans = {key: value for key, value in source_spans(work).items() if key != "T"}
        row = {"work_id": work_id, "title": work["title"]}
        started = perf_counter()
        try:
            raw = _post(config["api_root"] + "/api/generate", {
                "model": config["extractor_model"], "prompt": prompt(spans),
                "format": schema(spans), "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 300},
                "keep_alive": "5m"}, timeout=150)
            row["raw_response"] = raw.get("response")
            row["facets"] = validate(raw, spans)
            row["status"] = "valid_source_ids"
        except (ValueError, KeyError, TypeError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = round(perf_counter() - started, 3)
        rows.append(row)
        print(f"facets {len(rows)}/{len(works)}", flush=True)
    comparison = evaluate(pair_config, source, rows,
                          OllamaEmbedder(model=config["similarity_model"],
                                         url=config["api_root"]))
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "source_sha256": pair_config["source_sha256"],
            "pair_config_sha256": config["pair_config_sha256"],
            "extractor_model": config["extractor_model"],
            "extractor_digest": config["extractor_digest"],
            "similarity_model": config["similarity_model"],
            "similarity_digest": config["similarity_digest"],
            "article_facets": rows, **comparison,
            "baseline": {"holdout_balanced_accuracy": baseline["holdout_balanced_accuracy"],
                         "holdout_false_same_on_different": baseline["holdout_false_same_on_different"]},
            "limitations": config["limitations"], "production_changed": False,
            "independent_accuracy_measured": False, "weak_signal_accuracy_measured": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"article_count": result["article_count"],
                      "valid_article_facets": result["valid_article_facets"],
                      "holdout_diagnostics": result["holdout_diagnostics"]}), flush=True)
