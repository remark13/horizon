"""Diagnostic comparison of numbered-passage grounding on a frozen dev set.

The same twelve developer-labelled records were seen by an earlier method;
this is a development comparison, not an independent holdout evaluation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from urllib.error import URLError

from saia.controlled_collection import sha256_file
from saia.query_translation_pilot import LOCAL_API, _model_digest, _post
from saia.sentence_role_verifier import (SCHEMA, VERSION, passages, prompt,
                                         validate_response)
from scripts.benchmark_article_role_holdout import _inputs


def _evaluation_pairs(packet_path: Path, labels_path: Path) -> list[tuple[dict, dict]]:
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    if labels.get("version") == "role-verifier-holdout12-developer-labels-v1":
        return _inputs(packet_path, labels_path)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    expected = (labels_path.resolve().parents[1] /
                str(labels.get("packet") or "")).resolve()
    selected = (packet.get("items") or [])[12:24]
    rows = labels.get("rows") or []
    if (labels.get("version") != "role-verifier-dev12b-developer-labels-v1"
            or packet.get("version") != "cross-domain-article-relevance-packet-v1"
            or expected != packet_path.resolve()
            or labels.get("packet_sha256") != sha256_file(packet_path)
            or len(selected) != 12 or len(rows) != 12
            or [row.get("item_id") for row in rows]
               != [item["item_id"] for item in selected]
            or any(row.get("judgement") not in
                   {"direct", "related", "not_direct", "uncertain"} for row in rows)):
        raise ValueError("Frozen second development set provenance differs")
    return list(zip(selected, rows))


def run(packet_path: Path, labels_path: Path, *, model: str = "qwen3.5:9b",
        api_root: str = LOCAL_API, post=_post, model_digest=_model_digest) -> dict:
    if api_root != LOCAL_API:
        raise ValueError("Only local Ollama is allowed")
    pairs = _evaluation_pairs(packet_path, labels_path)
    digest = model_digest(api_root, model)
    if not digest:
        raise ValueError("Local model is not installed")
    rows = []
    for item, label in pairs:
        doc = item["document"]
        source_passages = passages(doc["title"], doc.get("abstract") or "")
        row = {"item_id": item["item_id"], "case_id": item["case_id"],
               "document_url": doc["url"], "target_topic": item["target_topic"],
               "developer_judgement": label["judgement"],
               "source_passages": source_passages}
        started = perf_counter()
        try:
            raw = post(api_root + "/api/generate", {
                "model": model,
                "prompt": prompt(item["target_topic"], source_passages),
                "format": SCHEMA, "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 300},
                "keep_alive": "5m",
            }, timeout=120)
            row["raw_model_response"] = raw.get("response")
            decision = validate_response(raw, source_passages)
            row.update({"status": "valid_model_response", "model_response": decision,
                        "agrees_with_developer": decision["decision"] == label["judgement"]})
        except (ValueError, KeyError, TimeoutError, URLError) as error:
            row.update({"status": "invalid_model_response",
                        "error_type": type(error).__name__,
                        "error_message": str(error)[:250]})
        row["seconds"] = round(perf_counter() - started, 3)
        rows.append(row)
    valid = [row for row in rows if row["status"] == "valid_model_response"]
    result = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "packet_sha256": sha256_file(packet_path),
            "developer_labels_sha256": sha256_file(labels_path),
            "model": model, "model_digest": digest,
            "rows": rows,
            "counts": {"total": len(rows), "valid": len(valid),
                       "valid_exact_agreement_with_developer": sum(
                           row["agrees_with_developer"] for row in valid),
                       "invalid": len(rows) - len(valid),
                       "false_direct_on_developer_nondirect": sum(
                           row["model_response"]["decision"] == "direct"
                           and row["developer_judgement"] in {"related", "not_direct", "uncertain"}
                           for row in valid)},
            "limitations": {"same_development_set_as_earlier_method": True,
                            "developer_labels_not_independent_gold": True,
                            "passage_selection_not_claim_entailment": True,
                            "title_abstract_only": True,
                            "no_automatic_production_use": True,
                            "not_weak_signal_accuracy": True}}
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    gate = labels.get("gate_before_model_run")
    if gate:
        result["predeclared_gate"] = gate
        result["predeclared_gate_passed"] = (
            result["counts"]["valid"] >= gate["min_valid_of_12"]
            and result["counts"]["valid_exact_agreement_with_developer"]
            >= gate["min_exact_agreement_with_developer_of_12"]
            and result["counts"]["false_direct_on_developer_nondirect"]
            <= gate["max_false_direct_on_developer_nondirect"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Diagnostic report is immutable")
    report = run(args.packet, args.labels, model=args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                        indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
