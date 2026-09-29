"""Frozen local-model feasibility test for article-to-line relevance.

The model sees only the blind review packet, never developer labels. A valid
verbatim quote is necessary but does not itself establish semantic correctness.
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
from scripts.probe_grounded_task_facets import _grounded, LOCAL_ROOTS


ROOT = Path(__file__).resolve().parents[1]
VERSION = "title-anchor-paper-llm-probe-v1"
ANSWERS = ["yes", "no", "unclear"]
ROLES = ["primary_result", "review", "application", "unclear"]
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "own_result": {"type": "string", "enum": ANSWERS},
        "bas_core": {"type": "string", "enum": ANSWERS},
        "role": {"type": "string", "enum": ROLES},
        "source_quote": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["own_result", "bas_core", "role", "source_quote", "reason"],
}


def prompt(case: dict) -> str:
    return (
        "Determine whether THIS article's OWN proposed method, data, benchmark, "
        "experiment, or system directly advances the proposed research line in "
        "the UAV/aerial-robot setting. Distinguish it from a survey, introductory "
        "motivation, a component merely USED by another proposed system, and a "
        "general method with UAVs only one example. Do not count a market or "
        "application label as a common technical method. Be conservative: use "
        "unclear when the abstract cannot settle the distinction. bas_core=yes "
        "only when the article's own contribution intrinsically studies UAVs; "
        "a multi-platform dataset or optional UAV application is not enough. "
        "role is primary_result, review, application, or unclear. source_quote "
        "must copy at least three consecutive words, verbatim, from the title "
        "or abstract and support the own_result decision. No invented quotes. "
        "Return JSON only.\n"
        f"Proposed line: {case['proposed_line_en']}\n"
        f"Title: {case['paper']['title']}\n"
        f"Abstract: {case['paper']['abstract']}\n"
    )


def parse_response(raw: dict, case: dict) -> dict:
    parsed = json.loads(raw["response"])
    if not isinstance(parsed, dict) or set(parsed) != set(SCHEMA["required"]):
        raise ValueError("Missing or unexpected response fields")
    if (parsed["own_result"] not in ANSWERS or parsed["bas_core"] not in ANSWERS
            or parsed["role"] not in ROLES):
        raise ValueError("Unknown categorical response")
    source = case["paper"]["title"] + " " + case["paper"]["abstract"]
    if not _grounded(parsed["source_quote"], source):
        raise ValueError("Ungrounded source quote")
    if not isinstance(parsed["reason"], str) or not parsed["reason"].strip():
        raise ValueError("Missing reason")
    return parsed


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION
            or config.get("purpose") != "development_diagnostic_only"
            or config.get("production_use") is not False
            or config.get("max_cases") != 28
            or not 1 <= config.get("max_run_seconds", 0) <= 900):
        raise ValueError("Invalid frozen diagnostic config")
    packet_path = (ROOT / config["blind_packet"]).resolve()
    if (not packet_path.is_relative_to(ROOT)
            or sha256_file(packet_path) != config["blind_packet_sha256"]):
        raise ValueError("Blind packet changed")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if packet.get("version") != "title-anchor-paper-review-v2" or len(packet["cases"]) != 28:
        raise ValueError("Unexpected blind packet")
    root = (api_root or os.environ.get("SAIA_OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    if root not in LOCAL_ROOTS:
        raise ValueError("Only local Ollama permitted")
    digest = model_digest(root, config["model"])
    if digest != config["model_digest"]:
        raise ValueError("Local model digest changed")
    rows = []
    started = perf_counter()
    for case in packet["cases"]:
        if perf_counter() - started >= config["max_run_seconds"]:
            break
        case_started = perf_counter()
        row = {"case_id": case["case_id"], "proposed_line_en": case["proposed_line_en"]}
        try:
            raw = post(root + "/api/generate", {
                "model": config["model"], "prompt": prompt(case),
                "format": SCHEMA, "stream": False, "think": False,
                "options": {"temperature": config["temperature"],
                            "num_predict": config["limit_output_tokens"]},
                "keep_alive": "5m",
            }, timeout=config["per_case_timeout_seconds"])
            row["raw_response"] = raw.get("response")
            row["prediction"] = parse_response(raw, case)
            row["status"] = "valid_grounded"
        except (ValueError, KeyError, TypeError, TimeoutError, URLError, OSError) as error:
            row.update({"status": "invalid", "error": f"{type(error).__name__}: {error}"[:250]})
        row["seconds"] = round(perf_counter() - case_started, 3)
        rows.append(row)
        if len(rows) % 4 == 0:
            print(json.dumps({"processed": len(rows), "total": len(packet["cases"]),
                              "seconds": round(perf_counter() - started, 1)}), flush=True)
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "blind_packet_sha256": config["blind_packet_sha256"],
            "model": config["model"], "model_digest": digest,
            "cases_processed": len(rows), "complete": len(rows) == len(packet["cases"]),
            "elapsed_seconds": round(perf_counter() - started, 3),
            "rows": rows, "production_changed": False,
            "semantic_accuracy_not_proven_by_grounded_quotes": True,
            "independent_expert_labels_loaded": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases_processed": result["cases_processed"],
                      "complete": result["complete"],
                      "elapsed_seconds": result["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
