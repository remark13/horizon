"""Development-only geometry diagnostics for saved topic compositions.

No holdout vectors are loaded by this module.  Feature comparison is
diagnostic and cannot produce a production threshold from the ten preliminary
single-annotator labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import yaml

from saia import db
from saia.candidates import parse_vector
from saia.composition_assessment import unit_vectors
from saia.composition_calibration import (
    POSITIVE_VERDICTS,
    apply_recheck,
    evidence_payload_sha256,
    read_json,
    validate_annotations,
)
from saia.hybrid import digest


POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "config/composition-feature-diagnostic.v0.4.11.yaml"
)


def _quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q, method="linear"))


def _two_cluster(matrix: np.ndarray, pairwise: np.ndarray) -> dict:
    n = len(matrix)
    upper = np.triu_indices(n, 1)
    flat_index = int(np.argmin(pairwise[upper]))
    seed_a, seed_b = upper[0][flat_index], upper[1][flat_index]
    labels = (matrix @ matrix[seed_b] > matrix @ matrix[seed_a]).astype(int)
    labels[seed_a], labels[seed_b] = 0, 1
    for _ in range(30):
        if not (labels == 0).any() or not (labels == 1).any():
            break
        centres = unit_vectors([
            matrix[labels == 0].mean(axis=0),
            matrix[labels == 1].mean(axis=0),
        ])
        scores = matrix @ centres.T
        updated = (scores[:, 1] > scores[:, 0]).astype(int)
        updated[seed_a], updated[seed_b] = 0, 1
        if np.array_equal(updated, labels):
            break
        labels = updated
    groups = [np.flatnonzero(labels == value) for value in (0, 1)]
    if any(len(group) == 0 for group in groups):
        return {
            "two_cluster_gain": None,
            "two_cluster_gap": None,
            "two_cluster_balance": 0.0,
        }
    within = []
    for group in groups:
        if len(group) >= 2:
            rows, cols = np.triu_indices(len(group), 1)
            within.extend(pairwise[group[rows], group[cols]].tolist())
    cross = pairwise[np.ix_(groups[0], groups[1])].ravel()
    all_pairs = pairwise[upper]
    within_mean = float(np.mean(within)) if within else None
    cross_mean = float(np.mean(cross))
    return {
        "two_cluster_gain": (
            within_mean - float(np.mean(all_pairs))
            if within_mean is not None else None
        ),
        "two_cluster_gap": (
            within_mean - cross_mean if within_mean is not None else None
        ),
        "two_cluster_balance": min(len(group) for group in groups) / n,
    }


def distributional_features(vectors: list) -> dict:
    matrix = unit_vectors(vectors)
    if matrix is None or len(matrix) < 3:
        raise ValueError("Для distributional coherence нужны минимум три вектора.")
    pairwise = np.clip(matrix @ matrix.T, -1.0, 1.0)
    upper = np.triu_indices(len(matrix), 1)
    pairs = pairwise[upper]
    mean_vector = matrix.mean(axis=0)
    mean_norm = float(np.linalg.norm(mean_vector))
    centroid_sims = None
    if math.isfinite(mean_norm) and mean_norm > 1e-12:
        centroid = mean_vector / mean_norm
        centroid_sims = np.clip(matrix @ centroid, -1.0, 1.0)
    nearest = np.max(pairwise - np.eye(len(matrix)) * 2.0, axis=1)
    robust_cutoff = None
    robust_outlier_fraction = None
    if centroid_sims is not None:
        median = float(np.median(centroid_sims))
        mad = float(np.median(np.abs(centroid_sims - median)))
        robust_cutoff = median - 3.0 * 1.4826 * mad
        robust_outlier_fraction = float(np.mean(centroid_sims < robust_cutoff))
    result = {
        "n_vectors": len(matrix),
        "mean_pairwise": float(np.mean(pairs)),
        "pairwise_q10": _quantile(pairs, 0.10),
        "pairwise_q25": _quantile(pairs, 0.25),
        "pairwise_min": float(np.min(pairs)),
        "centroid_mean": (
            float(np.mean(centroid_sims)) if centroid_sims is not None else None
        ),
        "centroid_q10": (
            _quantile(centroid_sims, 0.10) if centroid_sims is not None else None
        ),
        "centroid_min": (
            float(np.min(centroid_sims)) if centroid_sims is not None else None
        ),
        "nearest_neighbor_mean": float(np.mean(nearest)),
        "nearest_neighbor_q10": _quantile(nearest, 0.10),
        "robust_centroid_outlier_fraction": robust_outlier_fraction,
        "robust_centroid_outlier_cutoff": robust_cutoff,
    }
    result.update(_two_cluster(matrix, pairwise))
    if any(isinstance(value, float) and not math.isfinite(value)
           for value in result.values()):
        raise ValueError("Geometry feature must be finite or null.")
    return result


def collapse_research_families(work_ids: list[int], vectors: list,
                               families: list[list[int]]) -> tuple[list[int], list]:
    if len(work_ids) != len(vectors) or len(work_ids) != len(set(work_ids)):
        raise ValueError("Work IDs and vectors must be unique and aligned.")
    by_id = {work_id: vector for work_id, vector in zip(work_ids, vectors)}
    used = set()
    adjusted_ids: list[int] = []
    adjusted_vectors: list = []
    for family in families:
        members = list(family)
        if (len(members) < 2 or len(members) != len(set(members))
                or not set(members) <= set(by_id) or used.intersection(members)):
            raise ValueError("Некорректная или пересекающаяся research family.")
        used.update(members)
        matrix = unit_vectors([by_id[work_id] for work_id in members])
        adjusted_ids.append(min(members))
        adjusted_vectors.append(unit_vectors([matrix.mean(axis=0)])[0].tolist())
    for work_id in work_ids:
        if work_id not in used:
            adjusted_ids.append(work_id)
            adjusted_vectors.append(by_id[work_id])
    order = np.argsort(adjusted_ids)
    return ([adjusted_ids[index] for index in order],
            [adjusted_vectors[index] for index in order])


def _predict(value: float, threshold: float, orientation: str) -> bool:
    if orientation == "higher_supports_coherent_line":
        return value >= threshold
    if orientation == "lower_supports_coherent_line":
        return value <= threshold
    raise ValueError("Неизвестное направление feature.")


def _balanced_accuracy(rows: list[dict], feature: str, threshold: float,
                       orientation: str) -> tuple[float, int]:
    positives = [row for row in rows if row["target"]]
    negatives = [row for row in rows if not row["target"]]
    tpr = sum(_predict(row[feature], threshold, orientation)
              for row in positives) / len(positives)
    tnr = sum(not _predict(row[feature], threshold, orientation)
              for row in negatives) / len(negatives)
    errors = sum(_predict(row[feature], threshold, orientation) != row["target"]
                 for row in rows)
    return (tpr + tnr) / 2.0, errors


def fit_univariate(rows: list[dict], feature: str, orientation: str,
                   minimum_class_size: int) -> dict:
    usable = [row for row in rows if row.get(feature) is not None]
    positives = sum(row["target"] for row in usable)
    negatives = len(usable) - positives
    if positives < minimum_class_size or negatives < minimum_class_size:
        return {
            "status": "insufficient_class_size",
            "positive": positives,
            "negative": negatives,
            "threshold": None,
        }
    values = sorted({float(row[feature]) for row in usable})
    epsilon = max(1e-12, (max(values) - min(values)) * 1e-9)
    thresholds = [values[0] - epsilon]
    thresholds += [(left + right) / 2.0 for left, right in zip(values, values[1:])]
    thresholds.append(values[-1] + epsilon)
    scored = []
    for threshold in thresholds:
        accuracy, errors = _balanced_accuracy(usable, feature, threshold, orientation)
        scored.append((accuracy, errors, threshold))
    accuracy, errors, threshold = max(
        scored, key=lambda item: (item[0], -item[1], -abs(item[2]))
    )
    return {
        "status": "descriptive_only",
        "positive": positives,
        "negative": negatives,
        "threshold": threshold,
        "balanced_accuracy": round(accuracy, 6),
        "classification_errors": errors,
    }


def leave_one_out(rows: list[dict], feature: str, orientation: str,
                  minimum_class_size: int) -> dict:
    predictions = []
    for held in rows:
        train = [row for row in rows if row is not held]
        fitted = fit_univariate(train, feature, orientation, minimum_class_size)
        if fitted["threshold"] is None or held.get(feature) is None:
            return {"status": "not_available", "reason": "insufficient_fold"}
        predicted = _predict(held[feature], fitted["threshold"], orientation)
        predictions.append({
            "composition_sha256": held["composition_sha256"],
            "target": held["target"],
            "predicted": predicted,
        })
    positives = [row for row in predictions if row["target"]]
    negatives = [row for row in predictions if not row["target"]]
    tpr = sum(row["predicted"] for row in positives) / len(positives)
    tnr = sum(not row["predicted"] for row in negatives) / len(negatives)
    return {
        "status": "diagnostic_only",
        "balanced_accuracy": round((tpr + tnr) / 2.0, 6),
        "classification_errors": sum(
            row["predicted"] != row["target"] for row in predictions
        ),
        "predictions": predictions,
    }


def evaluate_features(rows: list[dict], policy: dict, prefix: str) -> dict:
    minimum = int(policy["evaluation"]["minimum_class_size"])
    diagnostics = {}
    for feature, feature_policy in policy["features"].items():
        name = f"{prefix}{feature}"
        orientation = feature_policy["orientation"]
        diagnostics[feature] = {
            "orientation": orientation,
            "fit": fit_univariate(rows, name, orientation, minimum),
            "leave_one_out": leave_one_out(rows, name, orientation, minimum),
        }
    ranked = sorted(
        diagnostics,
        key=lambda feature: (
            -(diagnostics[feature]["leave_one_out"].get("balanced_accuracy") or -1),
            diagnostics[feature]["leave_one_out"].get("classification_errors", 10**9),
            feature,
        ),
    )
    top_features = []
    if ranked:
        best_result = diagnostics[ranked[0]]["leave_one_out"]
        best_accuracy = best_result.get("balanced_accuracy")
        best_errors = best_result.get("classification_errors")
        top_features = [
            feature for feature in ranked
            if diagnostics[feature]["leave_one_out"].get("balanced_accuracy")
            == best_accuracy
            and diagnostics[feature]["leave_one_out"].get("classification_errors")
            == best_errors
        ]
    return {
        "features": diagnostics,
        "ranking_by_leave_one_out": ranked,
        "top_leave_one_out_features": top_features,
        "best_descriptive_feature": ranked[0] if ranked else None,
        "production_threshold": None,
        "production_calibration_allowed": False,
    }


def build_report(annotations: dict, label_evidence: dict, cards: dict,
                 current_evidence: dict, vector_rows: list[dict], policy: dict) -> dict:
    validation = validate_annotations(annotations, label_evidence)
    if digest(cards) != current_evidence.get("score_packet_sha256"):
        raise ValueError("Current cards не совпали с current evidence packet.")
    evidence_payload_sha256(current_evidence)
    label_cases = {case["composition_sha256"]: case for case in annotations["cases"]}
    current_cases = {case["composition_sha256"]: case for case in current_evidence["cases"]}
    if set(label_cases) != set(current_cases):
        raise ValueError("Текущая очередь изменила точные составы размеченных 15 кейсов.")
    development = {
        composition: case for composition, case in label_cases.items()
        if case["partition"] == "development"
    }
    if any(row["composition_sha256"] not in development for row in vector_rows):
        raise ValueError("В feature input попал holdout или неизвестный состав.")
    grouped: dict[str, list[dict]] = {composition: [] for composition in development}
    seen = set()
    for row in vector_rows:
        key = (row["composition_sha256"], row["work_id"])
        if key in seen or row.get("vector") is None:
            raise ValueError("Feature input содержит дубли или отсутствующий вектор.")
        seen.add(key)
        grouped[row["composition_sha256"]].append(row)
    evidence_by_composition = {
        hashlib.sha256(json.dumps(
            sorted(work["work_id"] for work in case["works"]),
            separators=(",", ":"),
        ).encode()).hexdigest(): case
        for case in label_evidence["cases"]
    }
    family_findings = {}
    for finding in annotations.get("research_family_findings", []):
        family_findings.setdefault(finding["composition_sha256"], []).append(
            finding["work_ids"]
        )
    rows = []
    for composition, case in sorted(development.items()):
        vectors = sorted(grouped[composition], key=lambda row: row["work_id"])
        expected_ids = sorted(
            work["work_id"] for work in evidence_by_composition[composition]["works"]
        )
        if [row["work_id"] for row in vectors] != expected_ids:
            raise ValueError("Development vector input не совпал с evidence composition.")
        raw = distributional_features([row["vector"] for row in vectors])
        adjusted_ids, adjusted_vectors = collapse_research_families(
            expected_ids,
            [row["vector"] for row in vectors],
            family_findings.get(composition, []),
        )
        adjusted = distributional_features(adjusted_vectors)
        row = {
            "composition_sha256": composition,
            "origin_candidate_id": case["candidate_id"],
            "current_candidate_id": current_cases[composition]["candidate_id"],
            "title": case["proposed_human_title"],
            "verdict": case["verdict"],
            "target": case["verdict"] in POSITIVE_VERDICTS,
            "raw_work_count": len(expected_ids),
            "adjusted_research_family_count": len(adjusted_ids),
            "declared_family_groups": family_findings.get(composition, []),
        }
        row.update({f"raw_{key}": value for key, value in raw.items()})
        row.update({f"family_adjusted_{key}": value for key, value in adjusted.items()})
        rows.append(row)
    result = {
        "version": "composition-geometry-diagnostic-0.4.11",
        "scope": "development_only",
        "annotation_version": annotations.get("version"),
        "recheck_overlay_version": annotations.get("recheck_overlay_version"),
        "mission_id": cards["mission_id"],
        "score_run_id": cards["score_run_id"],
        "bindings": {
            "annotation_sha256": digest(annotations),
            "recheck_overlay_sha256": annotations.get("recheck_overlay_sha256"),
            "label_evidence_payload_sha256": validation["payload_sha256"],
            "current_evidence_payload_sha256": current_evidence["report_payload_sha256"],
            "current_score_packet_sha256": digest(cards),
            "policy_sha256": digest(policy),
        },
        "policy": policy,
        "counts": {
            "development_compositions": len(rows),
            "positive": sum(row["target"] for row in rows),
            "negative": sum(not row["target"] for row in rows),
            "holdout_compositions": validation["internal_holdout_count"],
            "holdout_vectors_loaded": 0,
            "development_vectors_loaded": len(vector_rows),
        },
        "rows": rows,
        "raw_work_diagnostic": evaluate_features(rows, policy, "raw_"),
        "family_adjusted_diagnostic": evaluate_features(
            rows, policy, "family_adjusted_"
        ),
        "decision": {
            "production_feature": None,
            "production_threshold": None,
            "holdout_evaluated": False,
            "reason": policy["evaluation"]["reason"].strip(),
        },
        "scientific_weak_signal_accuracy_evaluated": False,
        "limitations": [
            policy["interpretation"].strip(),
            "Composition targets remain preliminary single-annotator judgements.",
            "Univariate leave-one-out on ten cases is a diagnostic, not external validation.",
            "The declared duplicate family is a manual sensitivity input, not an automatic merge.",
        ],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def load_development_vectors(cards: dict, annotations: dict) -> list[dict]:
    by_composition = {card["composition_sha256"]: card for card in cards["cards"]}
    selected = [case for case in annotations["cases"]
                if case["partition"] == "development"]
    if any(case["composition_sha256"] not in by_composition for case in selected):
        raise ValueError("Development composition отсутствует в current cards.")
    cluster = next(
        (run for run in cards["provenance"] if run["kind"] == "cluster"), None
    )
    if cluster is None or not cluster.get("embedding_model"):
        raise ValueError("Current cards не содержат cluster embedding provenance.")
    topics = {
        by_composition[case["composition_sha256"]]["topic_id"]:
        case["composition_sha256"]
        for case in selected
    }
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        with conn.cursor() as cur:
            cur.execute(
                """SELECT tm.topic_id,tm.work_id,e.embedding::text
                   FROM topic_membership tm
                   JOIN topic t USING(topic_id)
                   LEFT JOIN work_embedding e ON e.work_id=tm.work_id AND e.model=%s
                   WHERE t.run_id=%s AND tm.topic_id=ANY(%s)
                   ORDER BY tm.topic_id,tm.work_id""",
                (cluster["embedding_model"], cluster["run_id"], list(topics)),
            )
            rows = [
                {
                    "composition_sha256": topics[topic_id],
                    "work_id": work_id,
                    "vector": parse_vector(vector).tolist() if vector else None,
                }
                for topic_id, work_id, vector in cur.fetchall()
            ]
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--recheck", type=Path, required=True)
    parser.add_argument("--label-evidence", type=Path, required=True)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--current-evidence", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Выходной файл уже существует; нужен новый версионированный путь.")
    base = read_json(args.annotations)
    label_evidence = read_json(args.label_evidence)
    annotations = apply_recheck(base, read_json(args.recheck), label_evidence)
    cards = read_json(args.cards)
    current_evidence = read_json(args.current_evidence)
    policy = yaml.safe_load(args.policy.read_text(encoding="utf-8"))
    if (policy.get("scope") != "development_only"
            or policy.get("holdout_policy", {}).get("load_vectors") is not False
            or policy.get("evaluation", {}).get("production_threshold_allowed") is not False
            or policy.get("evaluation", {}).get("feature_combinations_allowed") is not False):
        raise ValueError("Feature policy нарушает development-only safety contract.")
    vectors = load_development_vectors(cards, annotations)
    report = build_report(
        annotations, label_evidence, cards, current_evidence, vectors, policy
    )
    report["generator_code_bytes_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    # Include the code hash in the final payload binding.
    report.pop("report_payload_sha256")
    report["report_payload_sha256"] = digest(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "development": report["counts"]["development_compositions"],
        "holdout_vectors_loaded": report["counts"]["holdout_vectors_loaded"],
        "best_raw": report["raw_work_diagnostic"]["best_descriptive_feature"],
        "best_family_adjusted": report["family_adjusted_diagnostic"]["best_descriptive_feature"],
        "report_payload_sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
