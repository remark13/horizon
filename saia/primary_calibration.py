"""Validate preliminary abstract-level primary-result labels.

The module compares the old title proxy with explicit abstract review.  It does
not modify scoring, does not infer scientific quality and cannot produce a
production G6 rule from one heavily positive development sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from saia.composition_calibration import PRIMARY_LABELS, read_json
from saia.hybrid import digest


METHOD_REASON_CODES = {
    "original_method_with_evaluation": (
        "Аннотация заявляет собственный метод или систему и сообщает её "
        "теоретическую либо эмпирическую проверку."
    ),
    "original_dataset_or_resource_with_evaluation": (
        "Аннотация представляет новый набор данных или исследовательский ресурс "
        "и сообщает его применение либо оценку."
    ),
    "original_empirical_study": (
        "Аннотация формулирует собственный исследовательский вопрос и сообщает "
        "полученные эмпирические результаты."
    ),
    "conceptual_framework_without_reported_evaluation": (
        "Аннотация предлагает нормативную или концептуальную рамку, но не "
        "сообщает собственную проверку с данными или экспериментом."
    ),
}

CLAIM_PATTERNS = (
    "we propose", "we introduce", "we present", "we develop", "we derive",
    "we revisit", "this paper introduces", "this study introduces",
    "this paper proposes", "this paper presents", "our research explores",
    "in this work, we show", "here, we introduce", "here, we focus",
    "we focus",
)
EVALUATION_PATTERNS = (
    "results show", "results demonstrate", "experimental results",
    "experiments show", "experiments demonstrate", "we demonstrate",
    "we show", "achieves", "outperforms", "we evaluate", "we validate",
    "we report", "we screen",
)


def _sentences(abstract: str) -> list[str]:
    return [value.strip() for value in re.split(r"(?<=[.!?])\s+", abstract.strip())
            if value.strip()]


def evidence_fragments(abstract: str) -> tuple[list[str], bool]:
    sentences = _sentences(abstract)
    if not sentences:
        raise ValueError("Нельзя разметить primary result без abstract evidence.")
    claim = next(
        (sentence for sentence in sentences
         if any(pattern in sentence.lower() for pattern in CLAIM_PATTERNS)),
        None,
    )
    evaluation = next(
        (sentence for pattern in EVALUATION_PATTERNS for sentence in sentences
         if sentence != claim and pattern in sentence.lower()),
        None,
    )
    fallback = claim is None
    fragments = [claim or sentences[0]]
    if evaluation is not None:
        fragments.append(evaluation)
    return fragments, fallback


def validate_and_enrich(queue: dict, decisions: dict, review_patterns: list[str]) -> dict:
    if decisions.get("not_gold_standard") is not True:
        raise ValueError("Primary decisions должны оставаться preliminary.")
    if decisions.get("base_queue_sha256") != digest(queue):
        raise ValueError("Primary decisions относятся к другой очереди.")
    queue_items = queue.get("items") or []
    by_work = {item.get("work_id"): item for item in queue_items}
    labels = decisions.get("labels") or []
    by_label = {item.get("work_id"): item for item in labels}
    if (None in by_work or None in by_label or len(by_work) != len(queue_items)
            or len(by_label) != len(labels) or set(by_work) != set(by_label)):
        raise ValueError("Каждая работа очереди должна быть размечена ровно один раз.")
    enriched = []
    fallbacks = 0
    for work_id, source in by_work.items():
        decision = by_label[work_id]
        label = decision.get("label")
        reason_code = decision.get("reason_code")
        if label not in PRIMARY_LABELS or reason_code not in METHOD_REASON_CODES:
            raise ValueError("Неизвестная primary label или reason code.")
        abstract = source.get("abstract")
        if not isinstance(abstract, str) or not abstract.strip():
            raise ValueError("Primary review требует непустой abstract.")
        fragments, fallback = evidence_fragments(abstract)
        if any(fragment not in abstract for fragment in fragments):
            raise ValueError("Evidence fragment должен быть точной частью abstract.")
        fallbacks += fallback
        title_lower = source["title"].lower()
        title_proxy = not any(pattern.lower() in title_lower for pattern in review_patterns)
        enriched.append({
            **source,
            "primary_result_label": label,
            "reason_code": reason_code,
            "rationale": METHOD_REASON_CODES[reason_code],
            "evidence_fragments": fragments,
            "evidence_extraction_fallback": fallback,
            "title_proxy_primary_candidate": title_proxy,
        })
    enriched.sort(key=lambda item: (item["candidate_id"], item["work_id"]))
    label_counts = Counter(item["primary_result_label"] for item in enriched)
    per_candidate = defaultdict(lambda: Counter())
    for item in enriched:
        per_candidate[item["candidate_id"]][item["primary_result_label"]] += 1
    proxy_tp = sum(item["title_proxy_primary_candidate"]
                   and item["primary_result_label"] == "primary_original_research"
                   for item in enriched)
    proxy_fp = sum(item["title_proxy_primary_candidate"]
                   and item["primary_result_label"] != "primary_original_research"
                   for item in enriched)
    proxy_fn = sum(not item["title_proxy_primary_candidate"]
                   and item["primary_result_label"] == "primary_original_research"
                   for item in enriched)
    result = {
        "version": "primary-result-calibration-0.4.12",
        "scope": "development_abstract_sample_only",
        "bindings": {
            "queue_sha256": digest(queue),
            "decisions_sha256": digest(decisions),
            "evidence_report_payload_sha256": queue["evidence_report_payload_sha256"],
            "annotation_sha256": queue.get("annotation_sha256"),
            "recheck_overlay_sha256": queue.get("recheck_overlay_sha256"),
        },
        "counts": {
            "items": len(enriched),
            "labels": dict(sorted(label_counts.items())),
            "evidence_extraction_fallbacks": fallbacks,
            "holdout_items_loaded": 0,
        },
        "per_candidate_sample": [
            {
                "candidate_id": candidate_id,
                "sample_size": sum(counts.values()),
                "primary_original_research": counts["primary_original_research"],
                "other": sum(count for label, count in counts.items()
                             if label != "primary_original_research"),
                "full_topic_primary_count_inferred": False,
            }
            for candidate_id, counts in sorted(per_candidate.items())
        ],
        "title_proxy_comparison": {
            "true_positive": proxy_tp,
            "false_positive": proxy_fp,
            "false_negative": proxy_fn,
            "precision_on_selected_sample": (
                proxy_tp / (proxy_tp + proxy_fp) if proxy_tp + proxy_fp else None
            ),
            "negative_label_count": len(enriched) - label_counts["primary_original_research"],
            "production_rule_allowed": False,
            "reason": (
                "Development sample is selected, single-annotator and contains "
                "only one non-primary label; it cannot calibrate G6."
            ),
        },
        "items": enriched,
        "decision": {
            "score_modified": False,
            "g6_production_rule": None,
            "holdout_evaluated": False,
            "scientific_quality_evaluated": False,
            "weak_signal_truth_evaluated": False,
        },
        "limitations": decisions.get("limitations", []),
    }
    result["report_payload_sha256"] = digest(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--methodology", type=Path,
                        default=Path(__file__).resolve().parents[1] /
                        "config/methodology.v0.4.yaml")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Выходной файл уже существует; нужен новый версионированный путь.")
    queue = read_json(args.queue)
    decisions = read_json(args.decisions)
    methodology = yaml.safe_load(args.methodology.read_text(encoding="utf-8"))
    patterns = methodology["publication_measurement"]["primary_review_patterns"]
    report = validate_and_enrich(queue, decisions, patterns)
    report["generator_code_bytes_sha256"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    report.pop("report_payload_sha256")
    report["report_payload_sha256"] = digest(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "counts": report["counts"],
        "title_proxy": report["title_proxy_comparison"],
        "report_payload_sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
