"""Apply a frozen BAS facet rule to already-labelled cross-domain development pairs.

The unlabelled holdout is deliberately never processed or inspected here.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

import numpy as np

from saia.controlled_collection import sha256_file
from saia.cross_domain_composition_review import validate as validate_review
from saia.embed import OllamaEmbedder
from saia.query_translation_pilot import _model_digest, _post
from scripts.probe_bas_structured_line_facets import FACETS, prompt, schema, unit, validate
from scripts.probe_grounded_task_facets_v3 import source_spans


ROOT = Path(__file__).resolve().parents[1]
VERSION = "cross-domain-structured-line-transfer-v1"


def frozen(config: dict, key: str) -> tuple[Path, dict]:
    path = (ROOT / config[key]).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != config[key + "_sha256"]:
        raise ValueError(f"Frozen {key} changed")
    return path, json.loads(path.read_text(encoding="utf-8"))


def selected(config: dict) -> tuple[list[dict], dict[int, dict]]:
    packet_path, packet = frozen(config, "packet")
    review_path, review = frozen(config, "developer_review")
    validate_review(packet_path, review_path)
    labels = {row["case_id"]: row["one_narrow_technology_line"]
              for row in review["labels"]}
    cases = [case for case in packet["cases"] if case["case_id"] in labels]
    if len(cases) != 12 or set(labels) != {case["case_id"] for case in cases}:
        raise ValueError("Frozen development case labels differ")
    if any(case["partition"] != "development" for case in cases):
        raise ValueError("Holdout case would be exposed")
    papers = {}
    for case in cases:
        for side in ("a", "b"):
            paper = case[f"paper_{side}"]
            identifier = paper["work_id"]
            if identifier in papers and papers[identifier] != paper:
                raise ValueError("Same work ID has different source text")
            papers[identifier] = paper
    if len(papers) != 24:
        raise ValueError("Expected 24 distinct development works")
    return cases, papers


def compare(cases: list[dict], rows: list[dict], embedder, threshold: float) -> dict:
    facets = {row["work_id"]: row for row in rows}
    texts, positions = [], {}
    for identifier in sorted(facets):
        row = facets[identifier]
        if row["status"] != "valid_source_ids":
            continue
        for name in FACETS:
            label = row["facets"][name]
            if label != "unknown":
                positions[identifier, name] = len(texts)
                texts.append(label)
    vectors = unit(embedder.embed(texts)) if texts else np.empty((0, 0))
    pairs = []
    for case in cases:
        a, b = case["paper_a"]["work_id"], case["paper_b"]["work_id"]
        similarities = {}
        for name in FACETS:
            x, y = positions.get((a, name)), positions.get((b, name))
            similarities[name] = round(float(np.dot(vectors[x], vectors[y])), 6) \
                if x is not None and y is not None else None
        minimum = min(similarities.values()) if all(
            value is not None for value in similarities.values()) else None
        both_primary = all(facets[i]["status"] == "valid_source_ids"
                           and facets[i]["facets"]["role"] == "primary_result"
                           for i in (a, b))
        prediction = (both_primary and minimum is not None
                      and minimum >= threshold)
        pairs.append({"case_id": case["case_id"], "domain": case["domain"],
                      "a": a, "b": b,
                      "minimum_facet_similarity": minimum,
                      "facet_cosines": similarities,
                      "both_primary_result": both_primary,
                      "predicted_narrow_line": prediction})
    return {"pairs": pairs, "threshold": threshold}


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION or config.get("production_use") is not False
            or config.get("api_root") != "http://127.0.0.1:11434"
            or config.get("primary_gate") !=
            "both papers primary_result, all three facet similarities present and minimum >= frozen BAS threshold"):
        raise ValueError("Unknown cross-domain transfer protocol")
    _, bas_result = frozen(config, "bas_facet_result")
    if bas_result["development_fits"]["min_three_facets"]["threshold"] != config["frozen_threshold"]:
        raise ValueError("BAS threshold changed")
    if (_model_digest(config["api_root"], config["extractor_model"])
            != config["extractor_digest"]
            or _model_digest(config["api_root"], config["similarity_model"])
            != config["similarity_digest"]):
        raise ValueError("Pinned model unavailable or changed")
    cases, papers = selected(config)
    _, review = frozen(config, "developer_review")
    labels = {row["case_id"]: row["one_narrow_technology_line"] == "yes"
              for row in review["labels"]}
    rows = []
    for identifier, paper in sorted(papers.items()):
        spans = {key: value for key, value in source_spans(paper).items() if key != "T"}
        row = {"work_id": identifier, "title": paper["title"],
               "source_urls": paper["source_urls"]}
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
        print(f"transfer facets {len(rows)}/{len(papers)}", flush=True)
    comparison = compare(cases, rows,
                         OllamaEmbedder(model=config["similarity_model"],
                                        url=config["api_root"]),
                         config["frozen_threshold"])
    for pair in comparison["pairs"]:
        pair["developer_narrow_line"] = labels[pair["case_id"]]
    positives = [row for row in comparison["pairs"] if row["developer_narrow_line"]]
    negatives = [row for row in comparison["pairs"] if not row["developer_narrow_line"]]
    if len(positives) != 1 or len(negatives) != 11:
        raise ValueError("Predeclared class counts differ")
    true_positive = sum(row["predicted_narrow_line"] for row in positives)
    true_negative = sum(not row["predicted_narrow_line"] for row in negatives)
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "packet_sha256": config["packet_sha256"],
            "developer_review_sha256": config["developer_review_sha256"],
            "bas_facet_result_sha256": config["bas_facet_result_sha256"],
            "article_facets": rows, **comparison,
            "metrics": {"positive_retained": true_positive,
                        "negative_separated": true_negative,
                        "false_merges": len(negatives) - true_negative,
                        "valid_article_facets": sum(row["status"] == "valid_source_ids"
                                                    for row in rows)},
            "predeclared_gate_passed": (
                true_positive >= config["acceptance"]["min_true_narrow_line_retained"]
                and true_negative >= config["acceptance"]["min_different_lines_separated"]),
            "limitations": config["limitations"],
            "holdout_cases_sent_to_model_or_scored": False,
            "production_changed": False,
            "independent_accuracy_measured": False}


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
    print(json.dumps({"metrics": result["metrics"],
                      "gate_passed": result["predeclared_gate_passed"]}), flush=True)
