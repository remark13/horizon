"""Compare frozen BAS paper-pair similarity using contribution-cue texts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from sklearn.feature_extraction.text import TfidfVectorizer

from saia.composition_features import fit_univariate
from saia.controlled_collection import sha256_file
from scripts.probe_bas_task_pair_similarity import _balanced, _source, _works, _validated_pairs


ROOT = Path(__file__).resolve().parents[1]
VERSION = "bas-claim-pair-similarity-v1"


def _frozen_json(config: dict, path_key: str) -> dict:
    path = (ROOT / config[path_key]).resolve()
    if not path.is_relative_to(ROOT) or sha256_file(path) != config[path_key + "_sha256"]:
        raise ValueError(f"Frozen {path_key} changed")
    return json.loads(path.read_text(encoding="utf-8"))


def _claim_texts(config: dict, works: dict[int, dict]) -> tuple[dict[int, str], list[int]]:
    index_dir = (ROOT / config["claim_index"]).resolve()
    if (not index_dir.is_relative_to(ROOT)
            or sha256_file(index_dir / "manifest.json") !=
            config["claim_index_manifest_sha256"]):
        raise ValueError("Frozen contribution index changed")
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    db_path = index_dir / manifest["file"]["name"]
    if (db_path.stat().st_size != manifest["file"]["bytes"]
            or sha256_file(db_path) != manifest["file"]["sha256"]):
        raise ValueError("Contribution index file changed")
    arxiv_ids = {}
    for work_id, work in works.items():
        identifiers = [row["value"] for row in work["identifiers"]
                       if row["kind"] == "arxiv"]
        if len(identifiers) != 1:
            raise ValueError("Pair work lacks one arXiv ID")
        arxiv_ids[identifiers[0]] = work_id
    if len(arxiv_ids) != len(works):
        raise ValueError("Pair works share an arXiv ID")
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    texts: dict[int, list[str]] = {identifier: [] for identifier in works}
    outside_parent = []
    try:
        for arxiv_id, work_id in arxiv_ids.items():
            found = conn.execute("SELECT 1 FROM works WHERE arxiv_id=?",
                                 (arxiv_id,)).fetchone()
            if not found:
                outside_parent.append(work_id)
                continue
            texts[work_id] = [row[0] for row in conn.execute(
                "SELECT text FROM claims WHERE arxiv_id=? "
                "AND format_hint!='review_or_synthesis' ORDER BY span_id", (arxiv_id,))]
    finally:
        conn.close()
    return ({identifier: " ".join(sentences) for identifier, sentences in texts.items()},
            sorted(outside_parent))


def evaluate(pair_config: dict, source: dict, claim_texts: dict[int, str],
             baseline: dict, *, outside_parent: list[int] | None = None) -> dict:
    works, card_of = _works(pair_config, source)
    pairs = _validated_pairs(pair_config, works, card_of)
    if set(claim_texts) != set(works):
        raise ValueError("Claim text and pair works differ")
    if baseline.get("pair_count") != len(pairs) or baseline.get("source_sha256") != pair_config["source_sha256"]:
        raise ValueError("Baseline pair universe differs")
    ids = sorted(works)
    texts = {
        "claim_tfidf_cosine": [claim_texts[identifier] for identifier in ids],
        "title_plus_claim_tfidf_cosine": [
            works[identifier]["title"] + " " + claim_texts[identifier]
            for identifier in ids],
    }
    vectors = {}
    vocab_sizes = {}
    for feature, documents in texts.items():
        transformer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english",
                                      sublinear_tf=True)
        vectors[feature] = transformer.fit_transform(documents)
        vocab_sizes[feature] = len(transformer.vocabulary_)
    positions = {identifier: offset for offset, identifier in enumerate(ids)}
    rows = []
    for pair in pairs:
        a, b = positions[pair["a"]], positions[pair["b"]]
        rows.append({"a": pair["a"], "b": pair["b"],
                     "partition": pair["partition"], "target": pair["same_task"],
                     **{feature: round(float((matrix[a] @ matrix[b].T).toarray()[0, 0]), 6)
                        for feature, matrix in vectors.items()}})
    fits = {}
    holdout = {}
    for feature in texts:
        fit = fit_univariate([row for row in rows if row["partition"] == "development"],
                             feature, "higher_supports_coherent_line", 5)
        fits[feature] = fit
        predictions = [{**row, "predicted": row[feature] >= fit["threshold"]}
                       for row in rows if row["partition"] == "holdout"]
        holdout[feature] = {
            "balanced_accuracy": _balanced(predictions),
            "false_same_on_different": sum(row["predicted"] and not row["target"]
                                           for row in predictions),
            "true_same_retained": sum(row["predicted"] and row["target"]
                                      for row in predictions),
            "threshold_from_development": fit["threshold"],
        }
    return {"version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "work_count": len(works), "pair_count": len(rows),
            "outside_claim_index_parent_work_ids": sorted(outside_parent or []),
            "works_without_claim_text": sum(not text for text in claim_texts.values()),
            "tfidf_vocabulary_sizes": vocab_sizes,
            "development_fits": fits, "holdout_diagnostics": holdout,
            "baseline": {"feature": baseline["selected_feature"],
                         "holdout_balanced_accuracy": baseline["holdout_balanced_accuracy"],
                         "holdout_false_same_on_different": baseline["holdout_false_same_on_different"]},
            "rows": rows, "production_changed": False,
            "independent_accuracy_measured": False,
            "weak_signal_accuracy_measured": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    if cfg.get("version") != VERSION or cfg.get("production_use") is not False:
        raise ValueError("Invalid claim-pair protocol")
    pair_config = _frozen_json(cfg, "pair_config")
    baseline = _frozen_json(cfg, "baseline_result")
    source = _source(pair_config)
    works, _ = _works(pair_config, source)
    claim_texts, outside_parent = _claim_texts(cfg, works)
    result = evaluate(pair_config, source, claim_texts, baseline,
                      outside_parent=outside_parent)
    result["config_sha256"] = sha256_file(args.config)
    result["claim_index_manifest_sha256"] = cfg["claim_index_manifest_sha256"]
    result["limitations"] = cfg["limitations"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"work_count": result["work_count"],
                      "works_without_claim_text": result["works_without_claim_text"],
                      "holdout_diagnostics": result["holdout_diagnostics"]}), flush=True)


if __name__ == "__main__":
    main()
