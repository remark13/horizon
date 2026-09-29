"""Pair comparison using exact source IDs for each paper's own contribution.

This is a previously opened developer diagnostic, not a production classifier.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from time import perf_counter
from urllib.error import URLError

from saia.controlled_collection import sha256_file
from saia.query_translation_pilot import _model_digest, _post
from scripts.probe_bas_pairwise_llm import _protocol, LOCAL_ROOTS
from scripts.probe_bas_task_pair_similarity import ROOT
from scripts.probe_grounded_task_facets_v3 import source_spans


VERSION = "bas-pairwise-contribution-ids-diagnostic-v2"
CONTRIBUTION = re.compile(
    r"\b(?:we\s+(?:propose|present|introduce|develop|design|demonstrate|investigate)"
    r"|this\s+(?:paper|work|article|study)\s+"
    r"(?:proposes|presents|introduces|develops|investigates|focuses)"
    r"|our\s+(?:method|system|approach|framework))\b", re.I,
)


def contribution_ids(spans: dict[str, str]) -> list[str]:
    found = [key for key, text in spans.items()
             if key != "T" and CONTRIBUTION.search(text)]
    return found or ["T"]


def _schema(ids_a: list[str], ids_b: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "same_task": {"type": "boolean"},
            "mechanism_relation": {"type": "string",
                                   "enum": ["same", "different", "unclear"]},
            "evidence_id_a": {"type": "string", "enum": ids_a},
            "evidence_id_b": {"type": "string", "enum": ids_b},
            "reason": {"type": "string"},
        },
        "required": ["same_task", "mechanism_relation", "evidence_id_a",
                     "evidence_id_b", "reason"],
    }


def _prompt(a: dict[str, str], b: dict[str, str],
            ids_a: list[str], ids_b: list[str]) -> str:
    def display(spans: dict[str, str]) -> str:
        return "\n".join(f"[{key}] {value}" for key, value in spans.items())

    return (
        "Compare the MAIN research TASK of two scientific papers. A shared "
        "field or vocabulary is not enough. The task is the outcome the "
        "authors try to achieve: what is estimated, located, planned, "
        "controlled or generated, and for which object. Different methods, "
        "datasets or settings can still address the SAME task. Conversely, "
        "locating the UAV itself differs from locating another object; "
        "finding a safe landing zone differs from estimating UAV pose; "
        "end-to-end navigation is not automatically explicit path planning. "
        "Also indicate separately whether the TECHNICAL MECHANISM is the "
        "same, different, or unclear. Cite one ID from EACH paper's own "
        "contribution sentences, not background motivation. Do not write "
        "a quote. Return JSON only.\n"
        "Allowed contribution IDs A: " + ", ".join(ids_a) + "\n"
        "Allowed contribution IDs B: " + ", ".join(ids_b) + "\n"
        "Paper A:\n" + display(a) + "\nPaper B:\n" + display(b)
    )


def _validate(raw: dict, a: dict[str, str], b: dict[str, str],
              ids_a: list[str], ids_b: list[str]) -> dict:
    value = json.loads(raw["response"])
    if (not isinstance(value, dict)
            or set(value) != {"same_task", "mechanism_relation",
                                  "evidence_id_a", "evidence_id_b", "reason"}
            or type(value["same_task"]) is not bool
            or value["mechanism_relation"] not in {"same", "different", "unclear"}
            or value["evidence_id_a"] not in ids_a
            or value["evidence_id_b"] not in ids_b
            or not isinstance(value["reason"], str) or not value["reason"].strip()):
        raise ValueError("Invalid or unsupported pair comparison")
    return {**value, "evidence_text_a": a[value["evidence_id_a"]],
            "evidence_text_b": b[value["evidence_id_b"]]}


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != "bas-pairwise-contribution-ids-v2"
            or config.get("model_role") != "development_diagnostic_only"
            or config.get("production_use") is not False):
        raise ValueError("Unknown or production-enabled pair protocol")
    # Reuse the frozen pair/source verifier from the original experiment.
    pair_config, _source, works, pairs = _protocol({
        "version": "bas-pairwise-llm-v1", "model_role": config["model_role"],
        "production_use": False, "pair_protocol": config["pair_protocol"],
        "pair_protocol_sha256": config["pair_protocol_sha256"],
    })
    root = (api_root or os.environ.get("SAIA_OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    if root not in LOCAL_ROOTS:
        raise ValueError("Only local Ollama is permitted")
    digest = model_digest(root, config["model"])
    if not digest:
        raise ValueError("Local model unavailable")
    spans_by_work = {identifier: source_spans(work)
                     for identifier, work in works.items()}
    ids_by_work = {identifier: contribution_ids(spans)
                   for identifier, spans in spans_by_work.items()}
    rows = []
    for pair in pairs:
        a, b = pair["a"], pair["b"]
        spans_a, spans_b = spans_by_work[a], spans_by_work[b]
        ids_a, ids_b = ids_by_work[a], ids_by_work[b]
        row = {"partition": pair["partition"], "a": a, "b": b,
               "developer_same_task": pair["same_task"]}
        started = perf_counter()
        try:
            raw = post(root + "/api/generate", {
                "model": config["model"],
                "prompt": _prompt(spans_a, spans_b, ids_a, ids_b),
                "format": _schema(ids_a, ids_b), "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 250},
                "keep_alive": "5m",
            }, timeout=150)
            row["raw_response"] = raw.get("response")
            value = _validate(raw, spans_a, spans_b, ids_a, ids_b)
            row.update({"status": "valid_source_ids", "comparison": value,
                        "agrees_with_developer":
                        value["same_task"] == pair["same_task"]})
        except (ValueError, KeyError, TypeError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = round(perf_counter() - started, 4)
        rows.append(row)
    valid = [row for row in rows if row["status"] == "valid_source_ids"]
    correct = {partition: sum(row.get("agrees_with_developer") is True
                              for row in rows if row["partition"] == partition)
               for partition in ("development", "holdout")}
    false_same = sum(row["comparison"]["same_task"] is True
                     for row in valid if row["partition"] == "holdout"
                     and row["developer_same_task"] is False)
    gate = config["gate"]
    return {
        "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "pair_protocol_sha256": config["pair_protocol_sha256"],
        "source_sha256": pair_config["source_sha256"],
        "model": config["model"], "model_digest": digest,
        "rows": rows,
        "counts": {"total": len(rows), "valid_source_ids": len(valid),
                   "development_correct_of_ten": correct["development"],
                   "holdout_correct_of_ten": correct["holdout"],
                   "holdout_false_same_on_different": false_same},
        "diagnostic_gate_passed": (
            len(valid) >= gate["valid_source_ids_min"]
            and correct["development"] >= gate["development_correct_min"]
            and correct["holdout"] >= gate["holdout_correct_min"]
            and false_same <= gate["holdout_false_same_max"]),
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
        raise FileExistsError("Pair diagnostic report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": result["counts"],
                      "diagnostic_gate_passed": result["diagnostic_gate_passed"]},
                     ensure_ascii=False))
