"""Frozen diagnostic: can lexical similarity separate mixed research lines?

This does not split production cards or claim weak-signal accuracy. The
threshold is selected on the old development partition only; the internal
holdout is read once for evaluation after that choice.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from saia.composition_features import fit_univariate
from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ("mean_pairwise", "pairwise_q10", "nearest_neighbor_q10")


def _read_source(config: dict, stem: str) -> dict:
    path = (ROOT / config[stem]).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != config[stem + "_sha256"]:
        raise ValueError(f"Frozen {stem} source differs")
    return json.loads(path.read_text(encoding="utf-8"))


def _text(work: dict) -> str:
    title = work["title"]
    return f"{title} {title} {title} {work.get('abstract') or ''}"


def _features(matrix, indexes: list[int]) -> dict:
    if len(indexes) < 3:
        raise ValueError("Composition needs at least three works")
    similarity = (matrix[indexes] @ matrix[indexes].T).toarray()
    pairwise = similarity[np.triu_indices(len(indexes), 1)]
    nearest = similarity.copy()
    np.fill_diagonal(nearest, -1.0)
    return {
        "n_works": len(indexes),
        "mean_pairwise": float(np.mean(pairwise)),
        "pairwise_q10": float(np.quantile(pairwise, 0.1)),
        "nearest_neighbor_q10": float(np.quantile(np.max(nearest, axis=1), 0.1)),
    }


def _balanced(predictions: list[dict]) -> float:
    positive = [row for row in predictions if row["target"]]
    negative = [row for row in predictions if not row["target"]]
    if not positive or not negative:
        raise ValueError("Evaluation requires both classes")
    tpr = sum(row["predicted"] for row in positive) / len(positive)
    tnr = sum(not row["predicted"] for row in negative) / len(negative)
    return (tpr + tnr) / 2


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != "lexical-line-coherence-pilot-v1"
            or config.get("candidate_features") != list(FEATURES)
            or config.get("production_use") is not False):
        raise ValueError("Unknown or production-enabled pilot")
    annotation = _read_source(config, "annotation")
    evidence = _read_source(config, "evidence")
    bas = _read_source(config, "bas_composition")
    annotations = {row["composition_sha256"]: row
                   for row in annotation["cases"]}
    compositions = {row["composition_sha256"]: row
                    for row in evidence["cases"]}
    if (len(annotations) != 15 or len(compositions) != 15
            or set(annotations) != set(compositions)
            or len(bas.get("cards") or []) != 24):
        raise ValueError("Frozen composition sets differ")

    works = {}
    for composition in compositions.values():
        for work in composition["works"]:
            work_id = work["work_id"]
            text = _text(work)
            if work_id in works and works[work_id] != text:
                raise ValueError("Same work ID has different text")
            works[work_id] = text
    ids = sorted(works)
    model = TfidfVectorizer(ngram_range=(1, 2), stop_words="english",
                            sublinear_tf=True)
    matrix = model.fit_transform([works[work_id] for work_id in ids])
    positions = {work_id: index for index, work_id in enumerate(ids)}
    rows = []
    for identifier, label in annotations.items():
        composition = compositions[identifier]
        indexes = [positions[work["work_id"]] for work in composition["works"]]
        rows.append({
            "composition_sha256": identifier,
            "partition": label["partition"], "verdict": label["verdict"],
            "target": label["verdict"] == "keep_after_outlier_removal",
            **_features(matrix, indexes),
        })
    development = [row for row in rows if row["partition"] == "development"]
    holdout = [row for row in rows if row["partition"] == "internal_holdout"]
    if len(development) != 10 or len(holdout) != 5:
        raise ValueError("Development/holdout partition changed")
    fits = {
        feature: fit_univariate(
            development, feature, "higher_supports_coherent_line",
            config["acceptance"]["minimum_development_class_size"],
        ) for feature in FEATURES
    }
    admissible = [feature for feature in FEATURES
                  if fits[feature]["status"] == "descriptive_only"]
    if not admissible:
        raise ValueError("No feature passed development class-size gate")
    selected = min(admissible, key=lambda feature: (
        -fits[feature]["balanced_accuracy"],
        fits[feature]["classification_errors"], feature,
    ))
    threshold = fits[selected]["threshold"]
    predictions = [{
        "composition_sha256": row["composition_sha256"],
        "verdict": row["verdict"], "target": row["target"],
        "predicted": row[selected] >= threshold,
    } for row in holdout]
    holdout_accuracy = _balanced(predictions)
    false_reject = sum(row["predicted"] for row in predictions
                       if row["verdict"] == "reject_as_single_line")
    accepted = (
        holdout_accuracy >= config["acceptance"]["minimum_internal_holdout_balanced_accuracy"]
        and false_reject <= config["acceptance"]["maximum_false_coherent_on_reject"]
    )

    # BAS is outside the training vocabulary and has no independent labels.
    # Show whether a document-only similarity signal is even numerically
    # available, but never transfer the AI threshold to these cases.
    bas_rows = []
    for card in bas["cards"][:2]:
        transformed = model.transform([_text(work) for work in card["works"]])
        values = _features(transformed, list(range(len(card["works"]))))
        bas_rows.append({"rank": card["rank"], "label": card["label"],
                         "composition_sha256": card["composition_sha256"],
                         **values, "threshold_transfer_allowed": False})
    return {
        "version": "lexical-line-coherence-diagnostic-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "annotation_sha256": config["annotation_sha256"],
        "evidence_sha256": config["evidence_sha256"],
        "bas_composition_sha256": config["bas_composition_sha256"],
        "distinct_ai_works": len(ids), "vocabulary_size": len(model.vocabulary_),
        "feature_fits_on_development": fits,
        "selected_feature": selected,
        "selected_threshold": threshold,
        "internal_holdout_balanced_accuracy": holdout_accuracy,
        "internal_holdout_predictions": predictions,
        "false_coherent_on_reject": false_reject,
        "acceptance_gate_passed": accepted,
        "bas_out_of_domain": bas_rows,
        "rows": rows,
        "interpretation": "Developer-only diagnostic; not independent signal accuracy or production calibration",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Diagnostic output is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "selected_feature", "internal_holdout_balanced_accuracy",
        "false_coherent_on_reject", "acceptance_gate_passed")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
