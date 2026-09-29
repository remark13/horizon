"""Does a cited per-paper task facet help separate two mixed BAS cards?

Diagnostic only: pair labels were previously opened, and a shared task is not
a shared method or a verified weak signal. No SAIA card or score is changed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

from saia.controlled_collection import sha256_file
from saia.query_translation_pilot import _model_digest, _post
from scripts.probe_grounded_task_facets import LOCAL_ROOTS
from scripts.probe_grounded_task_facets_v3 import (
    TASK_DESCRIPTIONS, _prompt, _schema, _validate, source_spans,
)


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-facet-pair-comparison-v1"


def _frozen_json(relative_path: str, expected_hash: str) -> dict:
    path = (ROOT / relative_path).resolve()
    if (not path.is_relative_to(ROOT) or sha256_file(path) != expected_hash):
        raise ValueError("Frozen BAS input differs")
    return json.loads(path.read_text(encoding="utf-8"))


def _works(source: dict) -> dict[int, dict]:
    cards = source.get("cards") or []
    if len(cards) != 24 or [card.get("rank") for card in cards[:2]] != [1, 2]:
        raise ValueError("Unexpected BAS source cards")
    works = {}
    for card in cards[:2]:
        for work in card["works"]:
            identifier = work["work_id"]
            if identifier in works or not work.get("title") or not work.get("abstract"):
                raise ValueError("Duplicate or textless BAS work")
            works[identifier] = work
    if len(works) != 26:
        raise ValueError("Expected 26 unique papers in the first two cards")
    return works


def score_pairs(pairs: list[dict], facets_by_work: dict[int, dict]) -> dict:
    """Conservative full-set scoring: abstention counts as an error."""
    rows = []
    for pair in pairs:
        a, b = pair["a"], pair["b"]
        if a not in facets_by_work or b not in facets_by_work:
            raise ValueError("Pair references an unprocessed work")
        left, right = facets_by_work[a], facets_by_work[b]
        valid = (left["status"] == "valid_source_ids"
                 and right["status"] == "valid_source_ids")
        left_family = left.get("facets", {}).get("task_family")
        right_family = right.get("facets", {}).get("task_family")
        predicted = (left_family == right_family
                     and left_family != "other_or_unclear") if valid else None
        rows.append({"partition": pair["partition"], "a": a, "b": b,
                     "developer_same_task": pair["same_task"],
                     "predicted_same_task": predicted,
                     "left_family": left_family, "right_family": right_family,
                     "match": valid and predicted == pair["same_task"]})
    metrics = {}
    for partition in ("development", "holdout"):
        subset = [row for row in rows if row["partition"] == partition]
        positives = [row for row in subset if row["developer_same_task"]]
        negatives = [row for row in subset if not row["developer_same_task"]]
        if len(positives) != 5 or len(negatives) != 5:
            raise ValueError("Expected five positive and five negative pairs")
        true_same = sum(row["predicted_same_task"] is True for row in positives)
        true_different = sum(row["predicted_same_task"] is False for row in negatives)
        metrics[partition] = {
            "pair_count": len(subset),
            "covered": sum(row["predicted_same_task"] is not None for row in subset),
            "true_same": true_same, "true_different": true_different,
            "false_same": sum(row["predicted_same_task"] is True for row in negatives),
            "false_different": sum(row["predicted_same_task"] is False for row in positives),
            "balanced_accuracy_abstentions_as_errors":
                (true_same / len(positives) + true_different / len(negatives)) / 2,
        }
    return {"rows": rows, "metrics": metrics}


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION or config.get("production_use") is not False
            or config.get("pair_prediction_rule") !=
            "same_non_other_task_family_only; invalid_article_facet_abstains"
            or any(family not in TASK_DESCRIPTIONS
                   for family in config["allowed_task_families"])):
        raise ValueError("Unknown BAS facet-pair protocol")
    source = _frozen_json(config["source"], config["source_sha256"])
    pair_protocol = _frozen_json(config["pair_protocol"],
                                 config["pair_protocol_sha256"])
    works = _works(source)
    pairs = pair_protocol["pairs"]
    if len(pairs) != 20 or any(p["a"] not in works or p["b"] not in works for p in pairs):
        raise ValueError("Frozen pair labels do not match the 26 papers")
    root = (api_root or os.environ.get("SAIA_OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    if root not in LOCAL_ROOTS:
        raise ValueError("Only local Ollama is permitted")
    digest = model_digest(root, config["model"])
    if not digest:
        raise ValueError("Frozen local model is unavailable")
    rows = []
    for identifier, work in sorted(works.items()):
        item = {"title": work["title"], "abstract": work["abstract"]}
        spans = source_spans(item)
        row = {"work_id": identifier, "title": work["title"]}
        started = perf_counter()
        try:
            raw = post(root + "/api/generate", {
                "model": config["model"],
                "prompt": _prompt(spans, config["allowed_task_families"]),
                "format": _schema(config["allowed_task_families"], list(spans)),
                "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 250},
                "keep_alive": "5m",
            }, timeout=150)
            row["raw_response"] = raw.get("response")
            row["facets"] = _validate(raw, spans, config["allowed_task_families"])
            row["status"] = "valid_source_ids"
        except (ValueError, KeyError, TypeError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = round(perf_counter() - started, 4)
        rows.append(row)
    by_work = {row["work_id"]: row for row in rows}
    scored = score_pairs(pairs, by_work)
    held = scored["metrics"]["holdout"]
    gate = config["acceptance"]
    valid_count = sum(row["status"] == "valid_source_ids" for row in rows)
    return {
        "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "source_sha256": config["source_sha256"],
        "pair_protocol_sha256": config["pair_protocol_sha256"],
        "model": config["model"], "model_digest": digest,
        "article_facets": rows, "article_count": len(rows),
        "valid_article_facets": valid_count,
        "pair_results": scored["rows"], "pair_metrics": scored["metrics"],
        "diagnostic_gate_passed": (
            valid_count >= gate["min_article_valid"]
            and held["covered"] >= gate["min_holdout_pair_coverage"]
            and held["balanced_accuracy_abstentions_as_errors"]
            >= gate["min_holdout_balanced_accuracy"]
            and held["false_same"] <= gate["max_holdout_false_same"]),
        "production_ready": False, "production_changed": False,
        "weak_signal_accuracy_measured": False,
        "limitations": config["limitations"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("BAS facet-pair report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"valid_article_facets": result["valid_article_facets"],
                      "pair_metrics": result["pair_metrics"],
                      "diagnostic_gate_passed": result["diagnostic_gate_passed"]},
                     ensure_ascii=False))
