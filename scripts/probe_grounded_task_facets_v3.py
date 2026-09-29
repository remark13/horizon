"""Development-only task-facet probe using selected source sentence IDs.

The model never supplies a quotation. Code resolves chosen IDs to immutable
source text, avoiding fabricated verbatim quotes. A correct ID does not prove
that the sentence supports the task or method label.
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
from scripts.probe_grounded_task_facets import LOCAL_ROOTS, _items


VERSION = "grounded-task-facets-sentence-id-probe-v3"
TASK_DESCRIPTIONS = {
    "uav_self_localization": "estimate the UAV's own pose or position",
    "aerial_object_localization": "detect or locate a target object in aerial imagery",
    "uav_mission_search_planning": "plan a high-level UAV search mission or task sequence",
    "uav_navigation_path_planning": "plan a flight path or route to a destination",
    "robotic_art_navigation": "navigate a flying art installation or artistic companion",
    "llm_compression": "compress a large language model",
    "agent_identity_access_control": "authenticate or authorize AI agents",
    "composite_material_design": "design a composite material structure",
    "breath_biometrics": "authenticate a person using breath or blowing",
    "other_or_unclear": "none of the above is supported as the MAIN task",
}


def source_spans(item: dict) -> dict[str, str]:
    """Stable source-bound IDs, not model-produced quotations."""
    spans = {"T": " ".join(item["title"].split())}
    abstract = " ".join(item["abstract"].split())
    pieces = [part.strip() for part in re.split(
        r"(?<=[.!?])\s+", abstract) if part.strip()]
    # The punctuation splitter above is intentionally conservative. It may
    # leave several clauses together, but never fabricates source wording.
    for index, part in enumerate(pieces, 1):
        spans[f"A{index}"] = part
    return spans


def _schema(families: list[str], span_ids: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "task_family": {"type": "string", "enum": families},
            "research_object": {"type": "string"},
            "task_evidence_id": {"type": "string", "enum": span_ids},
            "method_name": {"type": "string"},
            "method_evidence_id": {"type": "string", "enum": ["none", *span_ids]},
        },
        "required": ["task_family", "research_object", "task_evidence_id",
                     "method_name", "method_evidence_id"],
    }


def _prompt(spans: dict[str, str], families: list[str]) -> str:
    definitions = "\n".join(
        f"- {family}: {TASK_DESCRIPTIONS[family]}" for family in families)
    evidence = "\n".join(f"[{key}] {value}" for key, value in spans.items())
    return (
        "Classify the MAIN research task in this scientific paper, not its "
        "background motivation or possible use. Choose exactly one task "
        "family from the list. Select the source span ID that BEST SUPPORTS "
        "the task. Select another span ID for the actual method if a method "
        "is stated; otherwise use method_name='unknown' and "
        "method_evidence_id='none'. Do not copy or invent quotes. "
        "A paper's title can name a broad field while its abstract states "
        "a narrower task. If none of the named task families is the main "
        "task, choose other_or_unclear. Return JSON only.\n"
        "Task families:\n" + definitions + "\nSource spans:\n" + evidence
    )


def _validate(raw: dict, spans: dict[str, str], allowed: list[str]) -> dict:
    value = json.loads(raw["response"])
    keys = {"task_family", "research_object", "task_evidence_id",
            "method_name", "method_evidence_id"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Missing or unexpected facet keys")
    if any(not isinstance(value[key], str) for key in keys):
        raise ValueError("Facet values must be strings")
    if value["task_family"] not in allowed or value["task_evidence_id"] not in spans:
        raise ValueError("Task category or citation ID outside supplied source")
    method_id = value["method_evidence_id"]
    if method_id != "none" and method_id not in spans:
        raise ValueError("Method citation ID outside supplied source")
    if (method_id == "none") != (value["method_name"] == "unknown"):
        raise ValueError("Missing method citation must have unknown method")
    if not value["research_object"].strip():
        raise ValueError("Empty research object")
    return {**value,
            "task_evidence_text": spans[value["task_evidence_id"]],
            "method_evidence_text": None if method_id == "none" else spans[method_id]}


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION
            or config.get("model_role") != "development_diagnostic_only"
            or any(family not in TASK_DESCRIPTIONS
                   for family in config["allowed_task_families"])):
        raise ValueError("Unknown sentence-ID facet protocol")
    root = (api_root or os.environ.get("SAIA_OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    if root not in LOCAL_ROOTS:
        raise ValueError("Only local Ollama is permitted")
    items = _items(config)
    digest = model_digest(root, config["model"])
    if not digest:
        raise ValueError("Frozen local model unavailable")
    rows = []
    for item in items:
        spans = source_spans(item)
        row = {"source": item["source"], "id": item["id"],
               "title": item["title"], "url": item["url"],
               "developer_task_family": item["developer_task_family"]}
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
            facets = _validate(raw, spans, config["allowed_task_families"])
            row.update({"status": "valid_source_ids", "facets": facets,
                        "agrees_with_developer":
                        facets["task_family"] == item["developer_task_family"]})
        except (ValueError, KeyError, TypeError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = round(perf_counter() - started, 4)
        rows.append(row)
    valid = [row for row in rows if row["status"] == "valid_source_ids"]
    agreement = sum(row["agrees_with_developer"] for row in valid)
    critical_pairs = {
        ("aerial_object_localization", "uav_self_localization"),
        ("uav_self_localization", "aerial_object_localization"),
        ("robotic_art_navigation", "uav_mission_search_planning"),
    }
    critical = sum((row["developer_task_family"],
                    row["facets"]["task_family"]) in critical_pairs
                   for row in valid)
    gate = config["gate"]
    return {
        "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "bas_source_sha256": config["bas_source_sha256"],
        "cross_source_sha256": config["cross_source_sha256"],
        "model": config["model"], "model_digest": digest,
        "rows": rows,
        "counts": {"total": len(rows), "valid_source_ids": len(valid),
                   "task_family_agreement_with_developer": agreement,
                   "critical_errors": critical},
        "gate_passed": (len(valid) >= gate["valid_source_ids_min"]
                        and agreement >= gate["task_family_agreement_min"]
                        and critical <= gate["critical_errors_max"]),
        "limitations": config["limitations"],
        "source_id_is_structurally_valid_not_semantic_support": True,
        "production_changed": False,
        "production_ready": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Sentence-ID probe report is immutable")
    report = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "gate_passed": report["gate_passed"]},
                     ensure_ascii=False))
