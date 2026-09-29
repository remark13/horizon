"""Reproducible developer-label comparison of two BAS article-facet probes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


def score(config: dict, baseline: dict, stricter: dict) -> dict:
    ids = config["work_ids"]
    labels = config["pre_run_developer_core_uav_labels"]
    if set(labels) != {str(identifier) for identifier in ids}:
        raise ValueError("Frozen labels differ from selected articles")
    if ([row["work_id"] for row in baseline["rows"]] != ids
            or [row["work_id"] for row in stricter["rows"]] != ids):
        raise ValueError("Report articles or order differ")
    if baseline["model_digest"] != stricter["model_digest"]:
        raise ValueError("Different model weights")

    def metrics(report: dict, *, use_warning: bool) -> dict:
        correct = false_core = false_noncore = abstentions = covered = 0
        positives_accepted = positives_abstained = 0
        for row in report["rows"]:
            expected = labels[str(row["work_id"])]
            if row["status"] != "valid_source_ids":
                abstentions += 1
                if expected:
                    positives_abstained += 1
                continue
            facet = row["facets"]
            if (use_warning and facet["uav_role"] == "core_uav_research"
                    and facet["core_without_uav_in_own_claim_warning"]):
                abstentions += 1
                if expected:
                    positives_abstained += 1
                continue
            predicted = facet["uav_role"] == "core_uav_research"
            covered += 1
            correct += predicted == expected
            false_core += predicted and not expected
            false_noncore += not predicted and expected
            positives_accepted += predicted and expected
        return {
            "articles": len(ids), "covered": covered,
            "correct_with_abstentions_as_errors": correct,
            "false_core": false_core, "false_noncore": false_noncore,
            "abstentions": abstentions,
            "true_core_accepted": positives_accepted,
            "true_core_abstained": positives_abstained,
            "model_seconds": round(sum(row["seconds"] for row in report["rows"]), 3),
        }

    return {
        "version": "bas-article-facets-comparison-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label_origin": "one_developer_before_model_runs_not_independent_gold",
        "same_model_digest": baseline["model_digest"],
        "baseline": metrics(baseline, use_warning=False),
        "own_claim": metrics(stricter, use_warning=False),
        "own_claim_warning_as_abstention": metrics(stricter, use_warning=True),
        "quality_scope": "article_core_uav_role_only_not_signal_precision_at_15",
        "production_changed": False,
    }


def run(config_path: Path, baseline_path: Path, stricter_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    stricter = json.loads(stricter_path.read_text(encoding="utf-8"))
    if (baseline.get("config_sha256") != sha256_file(config_path)
            or stricter.get("config_sha256") != sha256_file(config_path)):
        raise ValueError("Model reports do not match frozen config")
    result = score(config, baseline, stricter)
    result["input_sha256"] = {
        "config": sha256_file(config_path),
        "baseline": sha256_file(baseline_path),
        "own_claim": sha256_file(stricter_path),
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--own-claim", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Comparison report is immutable")
    result = run(args.config, args.baseline, args.own_claim)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
