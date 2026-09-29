"""Zero-fit transfer check of local BGE relevance on pre-labelled cross-domain papers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from saia.controlled_collection import sha256_file
from scripts.probe_free_ru_bge_query_paper import ROOT, score_local_pairs


VERSION = "free-ru-bge-cross-domain-transfer-v1"


def evaluate_orders(rows: list[dict], checks: list[dict]) -> list[dict]:
    by_id = {row["item_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Duplicate cross-domain items")
    result = []
    for check in checks:
        high, low = by_id[check["higher"]], by_id[check["lower"]]
        if high["target_topic"] != low["target_topic"]:
            raise ValueError("Comparisons must use the same original topic")
        result.append({**check, "higher_score": high["score"],
                       "lower_score": low["score"],
                       "passed": high["score"] > low["score"]})
    return result


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != VERSION or config.get("production_use") is not False
            or config.get("input_policy") != "original_RU_target_topic_vs_title_plus_abstract"
            or config.get("max_length") != 320 or config.get("batch_size") != 4
            or len(config.get("predeclared_same_topic_order_checks") or []) != 4):
        raise ValueError("Unexpected transfer protocol")
    packet_path = (ROOT / config["packet"]).resolve()
    labels_path = (ROOT / config["developer_labels"]).resolve()
    model_dir = (ROOT / config["local_model_dir"]).resolve()
    if any(not path.is_relative_to(ROOT) for path in (packet_path, labels_path, model_dir)):
        raise ValueError("Transfer input outside project")
    if (sha256_file(packet_path) != config["packet_sha256"]
            or sha256_file(labels_path) != config["developer_labels_sha256"]):
        raise ValueError("Frozen transfer inputs changed")
    if not (model_dir / "model.safetensors").is_file():
        raise ValueError("Pinned local model absent")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    label_rows = json.loads(labels_path.read_text(encoding="utf-8"))["rows"]
    by_id = {row["item_id"]: row for row in packet["items"]}
    if len(label_rows) != 12 or len(by_id) < 12:
        raise ValueError("Unexpected frozen transfer population")
    selected = [by_id[row["item_id"]] for row in label_rows]
    pairs = [(item["target_topic"], item["document"]["title"] + ". " +
              (item["document"]["abstract"] or "")) for item in selected]
    scores, seconds = score_local_pairs(model_dir, pairs,
                                       batch_size=config["batch_size"],
                                       max_length=config["max_length"])
    if len(scores) != 12:
        raise ValueError("Incomplete model result")
    rows = [{"item_id": item["item_id"], "case_id": item["case_id"],
             "target_topic": item["target_topic"],
             "title": item["document"]["title"],
             "source_url": item["document"].get("url"),
             "developer_judgement": label["judgement"], "score": float(score)}
            for item, label, score in zip(selected, label_rows, scores)]
    checks = evaluate_orders(rows, config["predeclared_same_topic_order_checks"])
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "packet_sha256": sha256_file(packet_path),
            "developer_labels_sha256": sha256_file(labels_path),
            "model_repo": config["model_repo"],
            "model_revision": config["model_revision"],
            "model_safetensors_sha256": sha256_file(model_dir / "model.safetensors"),
            "inference_seconds_excluding_model_load": seconds,
            "items": rows, "checks": checks,
            "checks_passed": sum(row["passed"] for row in checks),
            "checks_total": len(checks),
            "production_changed": False, "weak_signal_quality_measured": False,
            "limitations": config["limitations"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Transfer report is immutable")
    result = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checks_passed": result["checks_passed"],
                      "checks_total": result["checks_total"],
                      "seconds": result["inference_seconds_excluding_model_load"]}))


if __name__ == "__main__":
    main()
