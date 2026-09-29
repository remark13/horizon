"""Bounded generic own-contribution pair probe on frozen development cases."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter

from saia.controlled_collection import sha256_file
from saia.cross_domain_composition_review import validate
from saia.query_translation_pilot import _model_digest, _post
from scripts.probe_grounded_task_facets import _grounded


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "evaluation/cross-domain-composition-pairs-2026-09-27-v2.json"
REVIEW = ROOT / "evaluation/cross-domain-composition-developer-review-2026-09-27-v1.json"
EXPECTED_PACKET_SHA = "2e92379a93aa81da98131fe5a818ba2b17241fd7b4fd13820cbb5cc4c2198fdc"
EXPECTED_REVIEW_SHA = "ea50161d3a2f5926a0c41559bb992d2b3de91f2a779ce741666ed36a52361b24"
MODEL = "qwen3.5:9b"
EXPECTED_MODEL_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "one_narrow_line": {"type": "boolean"},
        "line_name": {"type": "string"},
        "quote_a": {"type": "string"},
        "quote_b": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["one_narrow_line", "line_name", "quote_a", "quote_b", "reason"],
}


def prompt(case: dict) -> str:
    return (
        "Decide whether these papers are evidence for ONE narrow scientific-technology "
        "line, not merely the same research field. A line is a reusable technical "
        "principle addressing a specific target outcome in a defined application. "
        "Implementation variants can belong together if the principle and target "
        "outcome are shared. Shared words such as AI, robot, LLM, sensing, learning, "
        "or neuromorphic are insufficient. Compare each paper's OWN contribution, "
        "not background motivation or evaluation-only examples. A broad survey may "
        "provide context but cannot be a second primary result for a specific method. "
        "If yes, line_name must state principle + outcome + application in <=15 words. "
        "If no, line_name must be empty. quote_a and quote_b must each copy at least "
        "three consecutive words verbatim from the respective abstract to support "
        "the decision; no ellipses. Return JSON only.\n\n"
        f"Paper A title: {case['paper_a']['title']}\n"
        f"Paper A abstract: {case['paper_a']['abstract']}\n\n"
        f"Paper B title: {case['paper_b']['title']}\n"
        f"Paper B abstract: {case['paper_b']['abstract']}\n"
    )


def parse_response(response: str, case: dict) -> dict:
    parsed = json.loads(response)
    if not isinstance(parsed, dict) or set(parsed) != set(SCHEMA["required"]):
        raise ValueError("Missing or unexpected keys")
    if type(parsed["one_narrow_line"]) is not bool:
        raise ValueError("Invalid line decision")
    if parsed["one_narrow_line"] and not parsed["line_name"].strip():
        raise ValueError("Positive line has no name")
    if not parsed["one_narrow_line"] and parsed["line_name"].strip():
        raise ValueError("Negative line has a name")
    for side in ("a", "b"):
        quote = parsed[f"quote_{side}"]
        if not _grounded(quote, case[f"paper_{side}"]["abstract"]):
            raise ValueError(f"Ungrounded quote {side}")
    if not parsed["reason"].strip():
        raise ValueError("Missing reason")
    return parsed


def run(api_root: str) -> dict:
    if sha256_file(PACKET) != EXPECTED_PACKET_SHA or sha256_file(REVIEW) != EXPECTED_REVIEW_SHA:
        raise ValueError("Frozen diagnostic inputs changed")
    summary = validate(PACKET, REVIEW)
    if api_root not in {"http://127.0.0.1:11434", "http://host.docker.internal:11434"}:
        raise ValueError("Only local Ollama permitted")
    model_digest = _model_digest(api_root, MODEL)
    if model_digest != EXPECTED_MODEL_DIGEST:
        raise ValueError("Local model digest changed")
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in packet["cases"]}
    labels = {row["case_id"]: row["one_narrow_technology_line"] for row in review["labels"]}
    rows = []
    for case_id in labels:
        case = cases[case_id]
        started = perf_counter()
        row = {"case_id": case_id, "domain": case["domain"],
               "developer_label": labels[case_id]}
        try:
            response = _post(api_root + "/api/generate", {
                "model": MODEL, "prompt": prompt(case), "format": SCHEMA,
                "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 350},
                "keep_alive": "5m",
            }, timeout=120)
            row["raw_response"] = response.get("response")
            result = parse_response(response["response"], case)
            row.update({"status": "valid_grounded", "prediction": result,
                        "agrees_with_developer": result["one_narrow_line"] == (labels[case_id] == "yes")})
        except (ValueError, TypeError, KeyError, TimeoutError, OSError) as error:
            row.update({"status": "invalid", "error": f"{type(error).__name__}: {error}"[:250]})
        row["seconds"] = round(perf_counter() - started, 3)
        rows.append(row)
    valid = [row for row in rows if row["status"] == "valid_grounded"]
    return {"version": "cross-domain-shared-line-llm-probe-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "packet_sha256": summary["packet_sha256"],
            "review_sha256": summary["review_sha256"],
            "model": MODEL, "model_digest": model_digest,
            "rows": rows,
            "counts": {"total": len(rows), "valid_grounded": len(valid),
                       "developer_positive": sum(row["developer_label"] == "yes" for row in rows),
                       "developer_negative": sum(row["developer_label"] == "no" for row in rows),
                       "positive_retained": sum(row["developer_label"] == "yes" and
                                                row["prediction"]["one_narrow_line"]
                                                for row in valid),
                       "negative_separated": sum(row["developer_label"] == "no" and
                                                 not row["prediction"]["one_narrow_line"]
                                                 for row in valid),
                       "inference_seconds": round(sum(row["seconds"] for row in rows), 3)},
            "production_changed": False,
            "holdout_cases_loaded": 0,
            "limitations": ["Development labels by one researcher only",
                            "Only one positive naturally sampled pair",
                            "No claim of weak-signal detection or production quality"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    api_root = os.environ.get("SAIA_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    result = run(api_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False))
