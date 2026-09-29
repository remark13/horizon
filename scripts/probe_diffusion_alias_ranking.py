"""Four-way offline comparison; no database, models, source downloads or scout writes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from saia.cross_cluster_diffusion import DiffusionPolicy, find_phrase_candidates
from saia.emergence_priority import EmergencePriorityPolicy, rerank_observed_candidates
from saia.scientific_aliases import regular_plural_key
from scripts.probe_cross_cluster_diffusion import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def evaluate_family(result, family, documents, minimum_hits, maximum_rank):
    wanted = set(family["control_arxiv_ids"])
    controls = {row["id"]: item["value"] for row in documents for item in row["identifiers"]
                if item["kind"] == "arxiv" and item["value"] in wanted}
    expected_phrases = [regular_plural_key(phrase).split() for phrase in family["technical_phrases"]]
    hits = []
    for rank, row in enumerate(result["rows"], 1):
        names = [regular_plural_key(phrase).split() for phrase in
                 [row["phrase"], *row["cooccurring_phrases_same_works"]]]
        technical_name = any(any(name[offset:offset + len(needle)] == needle
                                 for offset in range(len(name) - len(needle) + 1))
                             for name in names for needle in expected_phrases)
        recovered = sorted({controls[e["work_id"]] for e in row["evidence"] if e["work_id"] in controls})
        if technical_name and recovered:
            hits.append({"rank": rank, "phrase": row["phrase"], "control_arxiv_ids": recovered,
                         "control_works": len(recovered), "candidate_works": len(row["evidence"]),
                         "control_fraction_in_candidate": len(recovered) / len(row["evidence"])})
    eligible = sorted(set(controls.values()))
    return {"id": family["id"], "eligible_control_ids": eligible,
            "control_ids_not_in_frozen_eligible_input": sorted(wanted - set(eligible)),
            "technical_name_control_hits": hits[:10],
            "technical_control_recovery_in_top15": any(
                row["rank"] <= maximum_rank and row["control_works"] >= minimum_hits for row in hits),
            "independent_precision": None, "coherent_line_verified": None}


def summarize_variant(result, controls, payload, acceptance):
    families = [*controls["new_families"], controls["known_regression"]]
    mature = []
    for phrase in controls["mature_bare_phrase_controls"]:
        needle = regular_plural_key(phrase)
        matches = [{"rank": rank, "phrase": row["phrase"], "recent_works": row["recent_works"]}
                   for rank, row in enumerate(result["rows"], 1) if regular_plural_key(row["phrase"]) == needle]
        mature.append({"bare_phrase": phrase, "matches": matches})
    return {"candidate_compositions": result["candidate_compositions"],
            "top15_phrases": [row["phrase"] for row in result["rows"][:15]],
            "families": [evaluate_family(result, family, payload["documents"],
                                         acceptance["minimum_control_works_in_proposal"],
                                         acceptance["maximum_review_rank"]) for family in families],
            "mature_bare_phrase_diagnostics_not_negative_labels": mature,
            "all_source_defined_acronym_hits_in_proposals": sum(
                span["basis"] == "source_defined_acronym" for row in result["rows"]
                for evidence in row["evidence"] for span in evidence.get("source_phrase_spans", [])),
            "primary_research_or_one_line_accuracy_measured": False}


def verify_source_spans(result, payload):
    by_id = {row["id"]: row for row in payload["documents"]}
    checked = 0
    for row in result["rows"]:
        for evidence in row["evidence"]:
            source = by_id[evidence["work_id"]]
            for span in evidence.get("source_phrase_spans", []):
                if source[span["field"]][span["start"]:span["end"]] != span["quote"]:
                    raise ValueError("Phrase evidence does not match its source")
                if "definition" in span:
                    definition = span["definition"]
                    if source[definition["field"]][definition["start"]:definition["end"]] != definition["quote"]:
                        raise ValueError("Acronym definition does not match the same work")
                checked += 1
    return checked


def compact_output(result, payload):
    # Keep all ranks, but detailed evidence for 15 displayed proposals only.
    # The frozen source and replay command recover any later proposal in full.
    keys = ("phrase", "cooccurring_phrases_same_works", "first_observed_window", "prior_works",
            "recent_works", "prior_share", "recent_share", "share_change", "active_area_gain",
            "recent_active_areas", "publication_composition_sha256", "review_priority_not_probability",
            "observed_emergence_priority_not_probability", "emergence_priority_components")
    by_id = {row["id"]: row for row in payload["documents"]}
    top15 = []
    for row in result["rows"][:15]:
        detailed = dict(row, evidence=[])
        for evidence in row["evidence"]:
            source = by_id[evidence["work_id"]]
            urls = ["https://arxiv.org/abs/" + item["value"] for item in source["identifiers"]
                    if item["kind"] == "arxiv"]
            detailed["evidence"].append(dict(evidence, date=source["date"], source_urls=urls))
        top15.append(detailed)
    return {"version": result["version"], "policy": result["policy"],
            "phrase_mode": result.get("phrase_mode", "exact"),
            "source_text_mode": result.get("source_text_mode", "literal"),
            "ranking_policy": result.get("ranking_policy"),
            "candidate_compositions": result["candidate_compositions"], "top15": top15,
            "all_candidate_ranks": [dict(rank=rank, **{key: row[key] for key in keys if key in row})
                                    for rank, row in enumerate(result["rows"], 1)],
            "limitations": result["limitations"], "production_changed": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    cfg = json.loads(args.config.read_text())
    if cfg.get("version") != "diffusion-alias-ranking-experiment-v1" or cfg.get("production_use") is not False:
        raise ValueError("Diagnostic protocol required")
    if not args.output_dir.parent.is_dir() or shutil.disk_usage(args.output_dir.parent).free < cfg["min_free_disk_reserve_bytes"]:
        raise ValueError("Missing output parent or insufficient disk reserve")
    source = ROOT / cfg["input"]
    if sha256_file(source) != cfg["input_file_sha256"]:
        raise ValueError("Frozen input file checksum mismatch")
    payload = json.loads(source.read_text())
    body = {key: value for key, value in payload.items() if key != "payload_sha256"}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != cfg["input_payload_sha256"] or digest != payload["payload_sha256"]:
        raise ValueError("Frozen input payload checksum mismatch")
    if len(payload["documents"]) > cfg["max_input_works"]:
        raise ValueError("Input resource cap exceeded")
    policy = DiffusionPolicy(**cfg["policy"])
    ranking_policy = EmergencePriorityPolicy(**cfg["ranking_policy"])
    policy.validate()
    ranking_policy.validate()
    started = time.monotonic()
    timings = {}
    variants = {}
    for name, mode in (("exact-original", "exact"), ("normalized-original", cfg["alias_policy"]["mode"])):
        stage = time.monotonic()
        result = find_phrase_candidates(payload["documents"], payload["windows"], policy=policy,
                                        coverage_comparable=False, phrase_mode=mode,
                                        source_text_mode=cfg.get("source_text_mode", "literal"))
        timings[name] = round(time.monotonic() - stage, 3)
        variants[name] = result
        stage = time.monotonic()
        variants[name.replace("original", "emergence")] = rerank_observed_candidates(result, ranking_policy)
        timings[name.replace("original", "emergence")] = round(time.monotonic() - stage, 3)
    source_spans_checked = verify_source_spans(variants["normalized-original"], payload)
    # No control publication IDs or technological names are read before generation.
    controls_path = ROOT / cfg["controls"]
    controls = json.loads(controls_path.read_text())
    if controls.get("version") != "diffusion-alias-ranking-controls-v1" or controls.get("independent_gold") is not False:
        raise ValueError("Evaluation-only controls required")
    comparison = {"version": cfg["version"], "created_at": datetime.now(timezone.utc).isoformat(),
                  "config_sha256": sha256_file(args.config), "controls_sha256": sha256_file(controls_path),
                  "input_file_sha256": cfg["input_file_sha256"], "input_payload_sha256": digest,
                  "source_code_sha256": {str(path.relative_to(ROOT)): sha256_file(path) for path in
                      [ROOT / "saia" / name for name in ("scientific_aliases.py", "source_text_view.py", "cross_cluster_diffusion.py", "emergence_priority.py")]
                      + [Path(__file__).resolve()]},
                  "policy": asdict(policy), "ranking_policy": asdict(ranking_policy),
                  "unique_input_works": len(payload["documents"]), "topics": len(payload["topics"]),
                  "stage_elapsed_seconds": timings, "source_phrase_spans_verified": source_spans_checked,
                  "source_text_mode": cfg.get("source_text_mode", "literal"),
                  "source_text_view_affected_works": variants["exact-original"].get("source_text_view_affected_works", 0),
                  "variants": {name: summarize_variant(result, controls, payload, cfg["acceptance"])
                               for name, result in variants.items()},
                  "production_changed": False, "independent_precision_measured": False,
                  "production_acceptance_passed": False, "limitations": cfg["limitations"]}
    comparison["analysis_elapsed_seconds"] = round(time.monotonic() - started, 3)
    outputs = {name + ".json": compact_output(result, payload) for name, result in variants.items()}
    outputs["comparison.json"] = comparison
    outputs["input-reference.json"] = {"path": cfg["input"], "bytes": source.stat().st_size,
                                       "file_sha256": cfg["input_file_sha256"], "payload_sha256": digest}
    encoded = {name: (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
               for name, value in outputs.items()}
    if sum(map(len, encoded.values())) > cfg["max_output_bytes"]:
        raise ValueError("Output resource cap exceeded")
    args.output_dir.mkdir()
    for name, content in encoded.items():
        (args.output_dir / name).write_bytes(content)
    print(json.dumps({"output_dir": str(args.output_dir), "output_bytes": sum(map(len, encoded.values())),
                      "analysis_seconds": comparison["analysis_elapsed_seconds"],
                      "source_spans_checked": source_spans_checked, "production_changed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
