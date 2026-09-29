"""Local-model paper-pair comparison; diagnostic only, never changes cards."""

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
from scripts.probe_bas_task_pair_similarity import ROOT, _source, _works, _validated_pairs
from scripts.probe_grounded_task_facets import _grounded


VERSION = "bas-pairwise-llm-diagnostic-v1"
LOCAL_ROOTS = {"http://127.0.0.1:11434", "http://host.docker.internal:11434"}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "same_task": {"type": "boolean"},
        "task_quote_a": {"type": "string"},
        "task_quote_b": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["same_task", "task_quote_a", "task_quote_b", "reason"],
}


def _protocol(config: dict) -> tuple[dict, dict, dict, list[dict]]:
    if (config.get("version") != "bas-pairwise-llm-v1"
            or config.get("model_role") != "development_diagnostic_only"
            or config.get("production_use") is not False):
        raise ValueError("Unknown or production-enabled pair comparison")
    path = (ROOT / config["pair_protocol"]).resolve()
    if (not path.is_relative_to(ROOT)
            or sha256_file(path) != config["pair_protocol_sha256"]):
        raise ValueError("Frozen pair protocol differs")
    pair_config = json.loads(path.read_text(encoding="utf-8"))
    source = _source(pair_config)
    works, card_of = _works(pair_config, source)
    return pair_config, source, works, _validated_pairs(pair_config, works, card_of)


def _prompt(a: dict, b: dict) -> str:
    return (
        "Compare the MAIN research task of two scientific papers. Decide if "
        "they address the same specific task; shared field words such as UAV, "
        "autonomy, vision and AI are not enough. Different methods or a "
        "dataset versus a method may still address the same task. Locating "
        "the UAV itself differs from locating a separate target object. "
        "General language-guided navigation is not automatically explicit "
        "flight-path planning. A flying art installation is not a regular "
        "search mission. Return JSON only. task_quote_a and task_quote_b "
        "must each copy at least THREE consecutive words and 12 characters "
        "EXACTLY from the corresponding title or abstract, including original "
        "punctuation; do not insert ellipses or paraphrase. These quotes must "
        "show what task each paper addresses. Give a one-sentence reason.\n\n"
        "Paper A title: " + a["title"] + "\nPaper A abstract: " +
        (a.get("abstract") or "") + "\n\nPaper B title: " + b["title"] +
        "\nPaper B abstract: " + (b.get("abstract") or "") + "\n"
    )


def _validate(raw: dict, a: dict, b: dict) -> dict:
    value = json.loads(raw["response"])
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        raise ValueError("Missing or unexpected pair keys")
    if type(value["same_task"]) is not bool:
        raise ValueError("same_task must be boolean")
    for key, paper in (("task_quote_a", a), ("task_quote_b", b)):
        if not isinstance(value[key], str) or not _grounded(
                value[key], paper["title"] + " " + (paper.get("abstract") or "")):
            raise ValueError(f"{key} is not grounded in the correct paper")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("Empty reason")
    return value


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pair_config, _source_packet, works, pairs = _protocol(config)
    root = (api_root or os.environ.get("SAIA_OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    if root not in LOCAL_ROOTS:
        raise ValueError("Only local Ollama is permitted")
    digest = model_digest(root, config["model"])
    if not digest:
        raise ValueError("Frozen local model is unavailable")
    rows = []
    for pair in pairs:
        started = perf_counter()
        row = {"partition": pair["partition"], "a": pair["a"], "b": pair["b"],
               "developer_same_task": pair["same_task"]}
        try:
            raw = post(root + "/api/generate", {
                "model": config["model"], "prompt": _prompt(
                    works[pair["a"]], works[pair["b"]]),
                "format": SCHEMA, "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 260},
                "keep_alive": "5m",
            }, timeout=150)
            row["raw_response"] = raw.get("response")
            result = _validate(raw, works[pair["a"]], works[pair["b"]])
            row.update({"status": "valid_grounded", "comparison": result,
                        "agrees_with_developer":
                        result["same_task"] == pair["same_task"]})
        except (ValueError, TypeError, KeyError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = perf_counter() - started
        rows.append(row)
    valid = [row for row in rows if row["status"] == "valid_grounded"]
    correct = {part: sum(row.get("agrees_with_developer") is True
                         for row in rows if row["partition"] == part)
               for part in ("development", "holdout")}
    false_same = sum(
        row["comparison"]["same_task"] is True
        for row in valid if row["partition"] == "holdout"
        and row["developer_same_task"] is False
    )
    gate = config["gate"]
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "pair_protocol_sha256": config["pair_protocol_sha256"],
        "source_sha256": pair_config["source_sha256"],
        "model": config["model"], "model_digest": digest,
        "rows": rows,
        "counts": {"total": len(rows), "valid_grounded": len(valid),
                   "development_correct_of_ten": correct["development"],
                   "holdout_correct_of_ten": correct["holdout"],
                   "holdout_false_same_on_different": false_same},
        "gate_passed": (
            len(valid) >= gate["valid_grounded_min"]
            and correct["development"] >= gate["development_correct_min"]
            and correct["holdout"] >= gate["holdout_correct_min"]
            and false_same <= gate["holdout_false_same_on_different_max"]
        ),
        "production_changed": False,
        "weak_signal_accuracy_measured": False,
        "limitations": config["limitations"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Pair diagnostic report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": result["counts"],
                      "gate_passed": result["gate_passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
