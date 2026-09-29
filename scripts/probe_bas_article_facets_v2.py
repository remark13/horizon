"""Contribution-grounded BAS article facet probe on the same frozen cohort.

This does not alter candidate generation. A valid sentence ID is structural
evidence only; developer and independent semantic checks remain necessary.
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
from scripts.probe_bas_article_facets import frozen_works
from scripts.probe_grounded_task_facets import LOCAL_ROOTS
from scripts.probe_grounded_task_facets_v3 import source_spans


VERSION = "bas-article-facets-own-claim-v2"
ROLES = ("core_uav_research", "uav_evaluation_only",
         "uav_motivation_or_application_only", "no_uav_research", "unclear")
OWN_CUE = re.compile(
    r"\b(?:we\s+(?:propose|present|introduce|develop|design|demonstrate|"
    r"investigate|explore|study|evaluate|test|show|prove|derive|report|"
    r"experiment|implement|analy[sz]e|tackle)"
    r"|(?:this|the)\s+(?:paper|work|article|study|letter|research)\s+"
    r"(?:proposes|presents|introduces|develops|investigates|focuses|"
    r"puts\s+forth|designs|evaluates|studies|shows|reports)"
    r"|our\s+(?:proposed\s+)?(?:method|system|approach|framework|"
    r"experiments|measurements|results|study)"
    r"|here\s*,?\s*we\s+(?:propose|present|explore|study|investigate))\b", re.I,
)
UAV_TERM = re.compile(r"\b(?:uavs?|drones?|quadrotors?|aerial\s+(?:robots?|vehicles?)|"
                      r"unmanned\s+(?:aircraft|aerial\s+vehicles?))\b", re.I)


def own_claim_ids(spans: dict[str, str]) -> list[str]:
    """Explicit own-work cues; unlike older probe, no title fallback."""
    return [key for key, value in spans.items()
            if key != "T" and OWN_CUE.search(value)]


def schema(spans: dict[str, str], own_ids: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "uav_role": {"type": "string", "enum": list(ROLES)},
            "uav_evidence_id": {"type": "string", "enum": ["none", *spans]},
            "contribution_id": {"type": "string", "enum": ["none", *own_ids]},
            "research_object": {"type": "string"},
            "main_task": {"type": "string"},
            "technical_method": {"type": "string"},
            "task_evidence_id": {"type": "string", "enum": ["none", *own_ids]},
            "method_evidence_ids": {"type": "array", "items": {
                "type": "string", "enum": list(spans)}, "minItems": 0, "maxItems": 3},
            "data_setting": {"type": "string", "enum": [
                "simulation", "physical_experiment_or_field", "both", "not_stated"]},
            "data_setting_evidence_id": {"type": "string", "enum": ["none", *spans]},
        },
        "required": ["uav_role", "uav_evidence_id", "contribution_id",
                     "research_object", "main_task", "technical_method",
                     "task_evidence_id", "method_evidence_ids", "data_setting",
                     "data_setting_evidence_id"],
    }


def prompt(spans: dict[str, str], own_ids: list[str]) -> str:
    evidence = "\n".join(f"[{key}] {value}" for key, value in spans.items())
    return (
        "Analyze THIS paper's own contribution, not introductory motivation. "
        "Select contribution_id and task_evidence_id ONLY from explicit own-work "
        "sentence IDs: " + (", ".join(own_ids) or "none") + ". If these do not "
        "state the task, use none and main_task='unknown'. Use up to three "
        "source sentence IDs for a compound technical method; if unstated use "
        "technical_method='unknown' and an empty method_evidence_ids list. "
        "Keep research_object, main_task and technical_method precise and "
        "distinct. For UAV role: core means the paper's OWN task/method "
        "intrinsically concerns UAVs; evaluation_only means a general method "
        "is merely tested on UAV data; motivation_or_application_only means "
        "UAVs appear only in motivation or prospective use; no_uav means no "
        "actual UAV study. Cite the sentence supporting this distinction. "
        "For data_setting, distinguish simulated data from physical/field "
        "measurements; a simulator based on a real place remains simulation. "
        "Use not_stated if unclear. Return JSON only.\nSource spans:\n" + evidence
    )


def validate(raw: dict, spans: dict[str, str], own_ids: list[str]) -> dict:
    value = json.loads(raw["response"])
    expected = set(schema(spans, own_ids)["required"])
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Missing or unexpected keys")
    if value["uav_role"] not in ROLES:
        raise ValueError("Unknown UAV role")
    for field in ("contribution_id", "task_evidence_id"):
        if value[field] != "none" and value[field] not in own_ids:
            raise ValueError("Not an own-claim sentence")
    for field in ("uav_evidence_id", "data_setting_evidence_id"):
        if value[field] != "none" and value[field] not in spans:
            raise ValueError("Evidence outside source")
    methods = value["method_evidence_ids"]
    if (not isinstance(methods, list) or len(methods) > 3
            or any(identifier not in spans for identifier in methods)):
        raise ValueError("Invalid method evidence")
    if (value["technical_method"] == "unknown") != (not methods):
        raise ValueError("Unknown method and missing evidence must agree")
    if value["data_setting"] not in {
            "simulation", "physical_experiment_or_field", "both", "not_stated"}:
        raise ValueError("Unknown setting")
    if any(not isinstance(value[field], str) or not value[field].strip()
           for field in ("research_object", "main_task", "technical_method")):
        raise ValueError("Empty facet")
    # A core claim justified solely by background is left visible as a
    # warning; this is not a semantic verifier or an automatic rejection.
    own_text = " ".join(spans[key] for key in own_ids)
    core_support_warning = (value["uav_role"] == "core_uav_research"
                            and not UAV_TERM.search(own_text))
    selected = {field: None if value[field] == "none" else spans[value[field]]
                for field in ("uav_evidence_id", "contribution_id",
                              "task_evidence_id", "data_setting_evidence_id")}
    return {**value, "source_evidence": selected,
            "method_evidence_texts": [spans[key] for key in methods],
            "core_without_uav_in_own_claim_warning": core_support_warning}


def run(config_path: Path, *, api_root: str | None = None,
        post=_post, model_digest=_model_digest) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("purpose") != "development_diagnostic_only"
            or config.get("production_use") is not False):
        raise ValueError("Only frozen diagnostic config permitted")
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
        own_ids = own_claim_ids(spans)
        row = {"work_id": work["work_id"], "title": work["title"],
               "own_claim_ids": own_ids}
        started = perf_counter()
        try:
            raw = post(root + "/api/generate", {
                "model": config["model"], "prompt": prompt(spans, own_ids),
                "format": schema(spans, own_ids), "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 400},
                "keep_alive": "5m",
            }, timeout=150)
            row["raw_response"] = raw.get("response")
            row["facets"] = validate(raw, spans, own_ids)
            row["status"] = "valid_source_ids"
        except (ValueError, KeyError, TypeError, TimeoutError, URLError) as error:
            row.update({"status": "invalid", "error_type": type(error).__name__,
                        "error": str(error)[:250]})
        row["seconds"] = round(perf_counter() - started, 3)
        rows.append(row)
    return {"version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "source_sha256": config["source_sha256"],
            "model": config["model"], "model_digest": digest,
            "counts": {"total": len(rows), "valid_source_ids": sum(
                row["status"] == "valid_source_ids" for row in rows)},
            "rows": rows, "production_changed": False,
            "semantic_accuracy_not_measured_by_source_ids": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
