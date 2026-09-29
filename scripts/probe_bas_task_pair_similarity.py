"""Read-only, single-developer diagnostic on saved BAS paper pairs.

The target is a shared research task, not a proven technological line. A
passing result never changes automatic candidates or their growth status.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from saia import db
from saia.candidates import parse_vector
from saia.composition_features import fit_univariate
from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ("specter_cosine", "title_abstract_tfidf_cosine")
VERSION = "bas-task-pair-similarity-diagnostic-v1"


def _source(config: dict) -> dict:
    path = (ROOT / config["source"]).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != config["source_sha256"]:
        raise ValueError("Frozen BAS source differs")
    return json.loads(path.read_text(encoding="utf-8"))


def _works(config: dict, source: dict) -> tuple[dict[int, dict], dict[int, int]]:
    if source.get("mission_id") != config["mission_id"]:
        raise ValueError("BAS mission differs")
    cards = source.get("cards") or []
    if len(cards) != 24:
        raise ValueError("Frozen BAS card count differs")
    works, card_of = {}, {}
    for card in cards[:2]:
        for work in card["works"]:
            identifier = int(work["work_id"])
            if identifier in works:
                raise ValueError("Work duplicated in first two cards")
            works[identifier] = work
            card_of[identifier] = card["rank"]
    return works, card_of


def _validated_pairs(config: dict, works: dict, card_of: dict) -> list[dict]:
    if (config.get("version") != "bas-task-pair-similarity-v1"
            or config.get("features") != list(FEATURES)
            or config.get("production_use") is not False
            or config.get("annotation_role") !=
            "single_developer_diagnostic_not_independent_gold"):
        raise ValueError("Unknown or production-enabled pair protocol")
    pairs = config.get("pairs") or []
    if len(pairs) != 20:
        raise ValueError("Expected twenty frozen pair judgements")
    seen, counts = set(), {}
    for row in pairs:
        if (set(row) != {"partition", "a", "b", "same_task", "reason"}
                or row["partition"] not in {"development", "holdout"}
                or type(row["a"]) is not int or type(row["b"]) is not int
                or type(row["same_task"]) is not bool or not row["reason"]):
            raise ValueError("Invalid pair judgement")
        a, b = row["a"], row["b"]
        key = tuple(sorted((a, b)))
        if (a == b or key in seen or a not in works or b not in works
                or card_of[a] != card_of[b]):
            raise ValueError("Pair is duplicate, missing or outside its parent card")
        seen.add(key)
        counter_key = (row["partition"], row["same_task"])
        counts[counter_key] = counts.get(counter_key, 0) + 1
    if any(counts.get((partition, target)) != 5
           for partition in ("development", "holdout") for target in (True, False)):
        raise ValueError("Each partition requires five positives and five negatives")
    return pairs


def _load_vectors(config: dict, works: dict) -> dict[int, np.ndarray]:
    ids = sorted(works)
    with db.connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT w.work_id,w.canonical_title,w.abstract,e.dim,e.embedding::text "
                "FROM work w JOIN work_embedding e USING(work_id) "
                "WHERE w.work_id=ANY(%s) AND w.mission_id=%s AND e.model=%s",
                (ids, config["mission_id"], config["embedding_model"]),
            )
            rows = cur.fetchall()
    if len(rows) != len(ids):
        raise ValueError("Saved SPECTER2 vectors are incomplete")
    result = {}
    for identifier, title, abstract, dim, vector_text in rows:
        frozen = works[identifier]
        if title != frozen["title"] or (abstract or "") != (frozen.get("abstract") or ""):
            raise ValueError("Embedding input text differs from frozen BAS source")
        vector = parse_vector(vector_text)
        if (vector is None or len(vector) != dim or dim != 768
                or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0):
            raise ValueError("Invalid saved SPECTER2 vector")
        result[identifier] = vector / np.linalg.norm(vector)
    return result


def _balanced(rows: list[dict]) -> float:
    positives = [row for row in rows if row["target"]]
    negatives = [row for row in rows if not row["target"]]
    if not positives or not negatives:
        raise ValueError("Both classes are required")
    return (sum(row["predicted"] for row in positives) / len(positives)
            + sum(not row["predicted"] for row in negatives) / len(negatives)) / 2


def evaluate(config: dict, source: dict, vectors: dict[int, np.ndarray]) -> dict:
    works, card_of = _works(config, source)
    pairs = _validated_pairs(config, works, card_of)
    if set(vectors) != set(works):
        raise ValueError("Expected one vector for every work in the first two cards")
    ids = sorted(works)
    tfidf = TfidfVectorizer(ngram_range=(1, 2), stop_words="english",
                            sublinear_tf=True)
    matrix = tfidf.fit_transform([
        f"{works[i]['title']} {works[i]['title']} {works[i]['title']} "
        f"{works[i].get('abstract') or ''}" for i in ids
    ])
    position = {identifier: n for n, identifier in enumerate(ids)}
    rows = []
    for pair in pairs:
        a, b = pair["a"], pair["b"]
        v1, v2 = vectors[a], vectors[b]
        if len(v1) != len(v2):
            raise ValueError("Pair vectors have different dimensions")
        rows.append({
            **pair,
            "parent_card_rank": card_of[a],
            "title_a": works[a]["title"], "title_b": works[b]["title"],
            "target": pair["same_task"],
            "specter_cosine": round(float(np.dot(v1, v2)), 6),
            "title_abstract_tfidf_cosine": round(float(
                (matrix[position[a]] @ matrix[position[b]].T).toarray()[0, 0]), 6),
        })
    development = [row for row in rows if row["partition"] == "development"]
    holdout = [row for row in rows if row["partition"] == "holdout"]
    fits = {feature: fit_univariate(
        development, feature, "higher_supports_coherent_line",
        config["acceptance"]["minimum_development_class_size"],
    ) for feature in FEATURES}
    selected = min(FEATURES, key=lambda name: (
        -fits[name]["balanced_accuracy"],
        fits[name]["classification_errors"], name,
    ))
    threshold = fits[selected]["threshold"]
    predictions = [{**row, "predicted": row[selected] >= threshold}
                   for row in holdout]
    balanced = _balanced(predictions)
    false_same = sum(row["predicted"] for row in predictions if not row["target"])
    gate = config["acceptance"]
    vector_digest = hashlib.sha256(b"".join(
        str(i).encode() + b":" + np.asarray(vectors[i], dtype="<f8").tobytes()
        for i in ids
    )).hexdigest()
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": config["source_sha256"],
        "embedding_model": config["embedding_model"],
        "input_vector_sha256": vector_digest,
        "work_count": len(works), "pair_count": len(rows),
        "tfidf_vocabulary_size": len(tfidf.vocabulary_),
        "feature_fits_on_development": fits,
        "selected_feature": selected, "selected_threshold": threshold,
        "holdout_balanced_accuracy": balanced,
        "holdout_false_same_on_different": false_same,
        "acceptance_gate_passed": (
            balanced >= gate["minimum_holdout_balanced_accuracy"]
            and false_same <= gate["maximum_false_same_on_different_holdout"]
        ),
        "holdout_predictions": predictions,
        "pairs": rows,
        "production_changed": False,
        "weak_signal_accuracy_measured": False,
        "limitations": config["limitations"],
    }


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source = _source(config)
    works, card_of = _works(config, source)
    _validated_pairs(config, works, card_of)
    result = evaluate(config, source, _load_vectors(config, works))
    result["config_sha256"] = sha256_file(config_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Diagnostic report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "selected_feature", "holdout_balanced_accuracy",
        "holdout_false_same_on_different", "acceptance_gate_passed")},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
