"""Test the unchanged local article-role prompt on a frozen cross-domain holdout.

The labels are developer judgements, not independent expert truth. No result
from this script changes the production retrieval or weak-signal ranking.
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
from scripts.diagnose_article_role_verifier import (
    DECISIONS, PROMPT, SCHEMA, _validate_response,
)


VERSION = "article-role-cross-domain-holdout-diagnostic-v1"


def _inputs(packet_path: Path, labels_path: Path) -> list[tuple[dict, dict]]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    expected_packet = (labels_path.resolve().parents[1] /
                       str(labels.get("packet") or "")).resolve()
    if (packet.get("version") != "cross-domain-article-relevance-packet-v1"
            or labels.get("version") != "role-verifier-holdout12-developer-labels-v1"
            or expected_packet != packet_path.resolve()
            or labels.get("packet_sha256") != sha256_file(packet_path)):
        raise ValueError("Frozen packet or label provenance differs")
    items, rows = packet.get("items"), labels.get("rows")
    if (not isinstance(items, list) or len(items) < 12
            or not isinstance(rows, list) or len(rows) != 12
            or [row["item_id"] for row in rows]
               != [item["item_id"] for item in items[:12]]):
        raise ValueError("Holdout is not exactly the first 12 frozen packet items")
    if any(row.get("judgement") not in DECISIONS for row in rows):
        raise ValueError("Unknown developer judgement")
    return list(zip(items[:12], rows))


def run(packet_path: Path, labels_path: Path, *, model: str = "qwen3.5:9b",
        api_root: str = LOCAL_API, post=_post, model_digest=_model_digest) -> dict:
    if api_root != LOCAL_API:
        raise ValueError("Only local Ollama is allowed")
    pairs = _inputs(packet_path, labels_path)
    digest = model_digest(api_root, model)
    if not digest:
        raise ValueError("Local model not installed")
    results = []
    for item, label in pairs:
        document = item["document"]
        title, abstract = document["title"], document.get("abstract") or ""
        prompt = (PROMPT + "User query: " + item["target_topic"] +
                  "\nTitle: " + title + "\nAbstract: " + abstract + "\n")
        row = {
            "item_id": item["item_id"], "case_id": item["case_id"],
            "target_topic": item["target_topic"],
            "document_url": document["url"], "document_title": title,
            "developer_judgement": label["judgement"],
            "developer_evidence_role": label["evidence_role"],
        }
        started = perf_counter()
        try:
            raw = post(api_root + "/api/generate", {
                "model": model, "prompt": prompt, "format": SCHEMA,
                "stream": False, "think": False,
                "options": {"temperature": 0, "num_predict": 300},
                "keep_alive": "5m",
            }, timeout=120)
            row["raw_model_response"] = raw.get("response")
            try:
                raw_decision = json.loads(raw["response"]).get("decision")
            except (json.JSONDecodeError, AttributeError, TypeError):
                raw_decision = None
            row["raw_decision"] = raw_decision if raw_decision in DECISIONS else None
            value = _validate_response(raw, title, abstract)
            row.update({"status": "valid_model_response", "model_response": value,
                        "agrees_with_developer": value["decision"] == label["judgement"]})
        except (ValueError, KeyError, TimeoutError, URLError) as error:
            row.update({"status": "invalid_model_response",
                        "error_type": type(error).__name__,
                        "error_message": str(error)[:250]})
        row["seconds"] = perf_counter() - started
        results.append(row)
    valid = [row for row in results if row["status"] == "valid_model_response"]
    raw = [row for row in results if row.get("raw_decision") in DECISIONS]
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "packet_sha256": sha256_file(packet_path),
        "developer_labels_sha256": sha256_file(labels_path),
        "model": model, "model_digest": digest,
        "unchanged_prompt_imported_from": "scripts.diagnose_article_role_verifier.PROMPT",
        "rows": results,
        "counts": {
            "total": len(results), "valid": len(valid),
            "valid_exact_agreement_with_developer": sum(
                row["agrees_with_developer"] for row in valid),
            "raw_decisions": len(raw),
            "raw_exact_agreement_with_developer": sum(
                row["raw_decision"] == row["developer_judgement"] for row in raw),
        },
        "limitations": {
            "developer_labels_not_independent_gold": True,
            "title_abstract_only": True,
            "prompt_originally_written_for_narrow_queries": True,
            "no_automatic_production_use": True,
            "not_weak_signal_accuracy": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Holdout diagnostic report is immutable")
    report = run(args.packet, args.labels, model=args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
