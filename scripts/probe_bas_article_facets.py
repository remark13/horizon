"""Frozen, source-grounded article-facet feasibility probe; never changes /scout.

The output checks source IDs, not semantic accuracy. Human assessment of the
selected sentence and three facet meanings is required before any use in
candidate grouping or ranking.
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
from scripts.probe_grounded_task_facets_v3 import source_spans


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-article-facets-pilot-v1"
ROLES = ("core_uav_research", "uav_as_possible_application", "no_uav_research", "unclear")
CONTRIBUTIONS = ("method", "dataset", "empirical_study", "review", "other_or_unclear")


def frozen_works(config: dict) -> list[dict]:
    path = (ROOT / config["source"]).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != config["source_sha256"]:
        raise ValueError("Frozen BAS source differs")
    report = json.loads(path.read_text(encoding="utf-8"))
    by_id: dict[int, dict] = {}
    for card in report["cards"]:
        for work in card["works"]:
            by_id.setdefault(work["work_id"], work)
    ids = config["work_ids"]
    if len(ids) != len(set(ids)) or not 5 <= len(ids) <= 20:
        raise ValueError("Invalid bounded work selection")
    if any(identifier not in by_id or not by_id[identifier].get("abstract") for identifier in ids):
        raise ValueError("Missing selected article or abstract")
    return [by_id[identifier] for identifier in ids]


def schema(span_ids: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "uav_role": {"type": "string", "enum": list(ROLES)},
            "uav_evidence_id": {"type": "string", "enum": ["none", *span_ids]},
            "contribution_type": {"type": "string", "enum": list(CONTRIBUTIONS)},
            "contribution_id": {"type": "string", "enum": ["none", *span_ids]},
            "research_object": {"type": "string"},
            "main_task": {"type": "string"},
            "technical_method": {"type": "string"},
            "task_evidence_id": {"type": "string", "enum": span_ids},
            "method_evidence_id": {"type": "string", "enum": ["none", *span_ids]}
        },
        "required": ["uav_role", "uav_evidence_id", "contribution_type",
                     "contribution_id", "research_object", "main_task",
                     "technical_method", "task_evidence_id", "method_evidence_id"]
    }


def prompt(spans: dict[str, str]) -> str:
    evidence = "\n".join(f"[{key}] {value}" for key, value in spans.items())
    return (
        "Extract the article's OWN research contribution, not a background claim "
        "or possible future application. Distinguish the research object (what "
        "is studied), the main task/problem (what is being improved or solved), "
        "and the technical method (how). Keep each phrase specific and short. "
        "Choose supporting source sentence IDs; never invent quotations. "
        "Choose contribution_id from a sentence that states what THIS paper "
        "does; use none for a review or if no such sentence exists. "
        "For uav_role: core_uav_research means the article actually studies "
        "UAVs; uav_as_possible_application means drones are only a motivation "
        "or possible use; no_uav_research means no actual UAV research. "
        "Use uav_evidence_id for the role, or none if no supporting span. "
        "For an unstated technical method set technical_method='unknown' and "
        "method_evidence_id='none'. Return JSON only.\nSource spans:\n" + evidence
    )


def validate(raw: dict, spans: dict[str, str]) -> dict:
    value = json.loads(raw["response"])
    expected = set(schema(list(spans))["required"])
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Missing or unexpected facet keys")
    if any(not isinstance(item, str) for item in value.values()):
        raise ValueError("Facet values must be strings")
    if value["uav_role"] not in ROLES or value["contribution_type"] not in CONTRIBUTIONS:
        raise ValueError("Unknown role or contribution")
    for field in ("uav_evidence_id", "contribution_id", "method_evidence_id"):
        if value[field] != "none" and value[field] not in spans:
            raise ValueError("Evidence ID outside source")
    if value["task_evidence_id"] not in spans:
        raise ValueError("Task evidence ID outside source")
    if (value["technical_method"] == "unknown") != (value["method_evidence_id"] == "none"):
        raise ValueError("Unknown method and missing citation must agree")
    if any(not value[field].strip() for field in ("research_object", "main_task")):
        raise ValueError("Empty object or task")
    return {**value, "source_evidence": {
        field: None if value[field] == "none" else spans[value[field]]
        for field in ("uav_evidence_id", "contribution_id", "task_evidence_id",
                      "method_evidence_id")
    }}


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION or config.get("purpose") != "development_diagnostic_only"
            or config.get("production_use") is not False):
        raise ValueError("Unknown or production-enabled probe protocol")
    works = frozen_works(config)
    root = (api_root or os.environ.get("SAIA_OLLAMA_URL")
            or "http://127.0.0.1:11434").rstrip("/")
    if root not in LOCAL_ROOTS:
        raise ValueError("Only local Ollama is permitted")
    digest = model_digest(root, config["model"])
    if not digest:
        raise ValueError("Frozen local model unavailable")
    rows = []
    for work in works:
        spans = source_spans(work)
        identifier = next((item["value"] for item in work.get("identifiers", [])
                           if item["kind"] == "arxiv"), None)
        row = {"work_id": work["work_id"], "title": work["title"],
               "arxiv_url": f"https://arxiv.org/abs/{identifier}" if identifier else None}
        started = perf_counter()
        try:
            raw = post(root + "/api/generate", {
                "model": config["model"], "prompt": prompt(spans),
                "format": schema(list(spans)), "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 330},
                "keep_alive": "5m",
            }, timeout=150)
            row["raw_response"] = raw.get("response")
            row["facets"] = validate(raw, spans)
            row["status"] = "valid_source_ids"
        except (ValueError, KeyError, TypeError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = round(perf_counter() - started, 3)
        rows.append(row)
    return {
        "version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path), "source_sha256": config["source_sha256"],
        "model": config["model"], "model_digest": digest, "rows": rows,
        "counts": {"total": len(rows), "valid_source_ids": sum(
            row["status"] == "valid_source_ids" for row in rows)},
        "source_id_validity_is_not_semantic_accuracy": True,
        "production_changed": False, "production_ready": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Pilot report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
