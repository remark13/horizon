"""Validate preliminary cluster-composition labels and diagnose coherence.

This module deliberately separates a composition judgement from a weak-signal
label.  It can refuse calibration, and it never treats an unlabelled title as
proof that a publication contains a primary research result.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from saia.hybrid import digest


ALLOWED_VERDICTS = {
    "keep_as_line",
    "keep_after_outlier_removal",
    "split_required",
    "reject_as_single_line",
}
POSITIVE_VERDICTS = {"keep_as_line", "keep_after_outlier_removal"}
NEGATIVE_VERDICTS = {"split_required", "reject_as_single_line"}
PARTITIONS = {"development", "internal_holdout"}
PRIMARY_LABELS = [
    "primary_original_research",
    "review_or_synthesis",
    "non_research_position_or_framework",
    "ambiguous_insufficient_metadata",
]
RECHECK_FIELDS = {
    "verdict", "outlier_work_ids", "proposed_human_title", "rationale",
}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON-артефакт должен быть объектом.")
    return value


def evidence_payload_sha256(evidence: dict) -> str:
    claimed = evidence.get("report_payload_sha256")
    payload = {key: value for key, value in evidence.items()
               if key != "report_payload_sha256"}
    actual = digest(payload)
    if claimed != actual:
        raise ValueError("Контрольная сумма evidence packet не совпала с содержимым.")
    return actual


def expected_partitions(split_seed: str, compositions: dict[int, str],
                        development_size: int) -> dict[int, str]:
    ranked = sorted(
        compositions,
        key=lambda candidate_id: hashlib.sha256(
            f"{split_seed}:{compositions[candidate_id]}".encode()
        ).hexdigest(),
    )
    return {
        candidate_id: ("development" if index < development_size
                       else "internal_holdout")
        for index, candidate_id in enumerate(ranked)
    }


def validate_annotations(annotations: dict, evidence: dict) -> dict:
    payload_sha = evidence_payload_sha256(evidence)
    if annotations.get("not_gold_standard") is not True:
        raise ValueError("Предварительную внутреннюю разметку нельзя объявлять gold standard.")
    if annotations.get("annotation_role") != "development_preliminary":
        raise ValueError("Поддерживается только явно предварительная development-разметка.")
    if annotations.get("mission_id") != evidence.get("mission_id"):
        raise ValueError("Миссия разметки и evidence packet различаются.")
    if annotations.get("score_run_id") != evidence.get("score_run_id"):
        raise ValueError("Score-прогон разметки и evidence packet различаются.")
    if annotations.get("evidence_report_payload_sha256") != payload_sha:
        raise ValueError("Разметка относится к другому evidence packet.")

    evidence_cases = evidence.get("cases") or []
    annotations_cases = annotations.get("cases") or []
    by_evidence = {case.get("candidate_id"): case for case in evidence_cases}
    by_annotation = {case.get("candidate_id"): case for case in annotations_cases}
    if (None in by_evidence or None in by_annotation
            or len(by_evidence) != len(evidence_cases)
            or len(by_annotation) != len(annotations_cases)):
        raise ValueError("Candidate ID должен быть задан и встречаться ровно один раз.")
    if set(by_annotation) != set(by_evidence):
        raise ValueError("Разметка должна покрывать каждый evidence-кейс ровно один раз.")

    split = annotations.get("split_policy") or {}
    development_size = split.get("development_size")
    holdout_size = split.get("internal_holdout_size")
    if (not isinstance(development_size, int) or not isinstance(holdout_size, int)
            or development_size < 1 or holdout_size < 1
            or development_size + holdout_size != len(by_evidence)):
        raise ValueError("Некорректный размер development/holdout-разбиения.")
    split_seed = split.get("split_seed")
    if not isinstance(split_seed, str) or not split_seed.strip():
        raise ValueError("Для устойчивого разбиения нужен непустой split_seed.")
    compositions = {
        candidate_id: hashlib.sha256(
            json.dumps(
                sorted(work["work_id"] for work in case.get("works", [])),
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for candidate_id, case in by_evidence.items()
    }
    expected = expected_partitions(split_seed, compositions, development_size)

    counts = Counter()
    for candidate_id, case in by_annotation.items():
        source = by_evidence[candidate_id]
        if case.get("composition_sha256") != compositions[candidate_id]:
            raise ValueError(f"Кандидат {candidate_id}: хэш состава не совпал с evidence.")
        if case.get("partition") not in PARTITIONS or case["partition"] != expected[candidate_id]:
            raise ValueError(f"Кандидат {candidate_id}: разбиение не соответствует hash-policy.")
        if case.get("source_label") != source.get("label"):
            raise ValueError(f"Кандидат {candidate_id}: исходная метка изменилась.")
        verdict = case.get("verdict")
        if verdict not in ALLOWED_VERDICTS:
            raise ValueError(f"Кандидат {candidate_id}: неизвестный composition verdict.")
        work_ids = {work.get("work_id") for work in source.get("works", [])}
        outliers = case.get("outlier_work_ids")
        if (not isinstance(outliers, list) or len(outliers) != len(set(outliers))
                or not set(outliers) <= work_ids):
            raise ValueError(f"Кандидат {candidate_id}: неверный список выбросов.")
        if verdict == "keep_as_line" and outliers:
            raise ValueError(f"Кандидат {candidate_id}: keep_as_line не допускает выбросы.")
        if verdict == "keep_after_outlier_removal":
            retained_share = (len(work_ids) - len(outliers)) / len(work_ids)
            if not outliers or retained_share < 0.70:
                raise ValueError(
                    f"Кандидат {candidate_id}: сохранённое ядро меньше policy 70%."
                )
        if (not isinstance(case.get("proposed_human_title"), str)
                or len(case["proposed_human_title"].strip()) < 10
                or not isinstance(case.get("rationale"), str)
                or len(case["rationale"].strip()) < 40
                or case.get("review_basis") != "titles_and_available_abstracts"):
            raise ValueError(f"Кандидат {candidate_id}: неполное объяснение разметки.")
        counts[(case["partition"], verdict in POSITIVE_VERDICTS)] += 1

    excluded = set((annotations.get("label_policy") or {}).get("excluded_claims", []))
    required_exclusions = {
        "weak_signal_truth", "future_growth", "market_success",
        "commercial_adoption", "primary_research_status",
    }
    if not required_exclusions <= excluded:
        raise ValueError("Разметка должна явно исключать научные и рыночные утверждения.")
    return {
        "payload_sha256": payload_sha,
        "candidate_count": len(by_evidence),
        "development_count": development_size,
        "internal_holdout_count": holdout_size,
        "partition_target_counts": {
            f"{partition}_{'positive' if target else 'negative'}": count
            for (partition, target), count in sorted(counts.items())
        },
    }


def apply_recheck(annotations: dict, recheck: dict, evidence: dict) -> dict:
    """Apply a versioned adjudication overlay without rewriting the base file."""
    validate_annotations(annotations, evidence)
    if recheck.get("not_gold_standard") is not True:
        raise ValueError("Повторная внутренняя проверка не может быть gold standard.")
    if recheck.get("base_annotation_sha256") != digest(annotations):
        raise ValueError("Recheck overlay относится к другой базовой разметке.")
    if recheck.get("evidence_report_payload_sha256") != evidence_payload_sha256(evidence):
        raise ValueError("Recheck overlay относится к другому evidence packet.")
    changes = recheck.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("Recheck overlay должен содержать непустой список changes.")
    merged = copy.deepcopy(annotations)
    by_composition = {case["composition_sha256"]: case for case in merged["cases"]}
    seen = set()
    for change in changes:
        composition = change.get("composition_sha256")
        if composition in seen or composition not in by_composition:
            raise ValueError("Recheck содержит повторный или неизвестный composition_sha256.")
        seen.add(composition)
        case = by_composition[composition]
        if change.get("origin_candidate_id") != case["candidate_id"]:
            raise ValueError("Recheck candidate ID не совпал с базовой разметкой.")
        updates = change.get("set")
        if (not isinstance(updates, dict) or not updates
                or not set(updates) <= RECHECK_FIELDS):
            raise ValueError("Recheck пытается изменить запрещённые поля.")
        case.update(copy.deepcopy(updates))
        case["rechecked"] = True
        case["recheck_reason"] = change.get("reason_for_change")
    merged["base_version"] = annotations["version"]
    merged["version"] = "current-triage-composition-0.4.11-preliminary-rechecked"
    merged["review_scope"] = recheck.get("review_scope")
    merged["review_limitations"] = copy.deepcopy(recheck.get("review_limitations") or [])
    merged["research_family_findings"] = copy.deepcopy(
        recheck.get("research_family_findings") or []
    )
    merged["recheck_overlay_version"] = recheck.get("version")
    merged["recheck_overlay_sha256"] = digest(recheck)
    validate_annotations(merged, evidence)
    return merged


def _balanced_accuracy(rows: list[dict], threshold: float) -> tuple[float, int]:
    positives = [row for row in rows if row["target"]]
    negatives = [row for row in rows if not row["target"]]
    tpr = sum(row["coherence"] >= threshold for row in positives) / len(positives)
    tnr = sum(row["coherence"] < threshold for row in negatives) / len(negatives)
    errors = sum((row["coherence"] >= threshold) != row["target"] for row in rows)
    return (tpr + tnr) / 2, errors


def coherence_diagnostic(annotations: dict, cards: dict) -> dict:
    by_card = {card.get("candidate_id"): card for card in cards.get("cards", [])}
    rows = []
    for case in annotations["cases"]:
        card = by_card.get(case["candidate_id"])
        if card is None:
            raise ValueError(f"В score packet нет кандидата {case['candidate_id']}.")
        coherence = ((card.get("metrics") or {}).get("observed") or {}).get("coherence")
        if (not isinstance(coherence, (int, float)) or isinstance(coherence, bool)
                or not math.isfinite(coherence) or not -1 <= coherence <= 1):
            raise ValueError(f"У кандидата {case['candidate_id']} нет корректной связности.")
        rows.append({
            "candidate_id": case["candidate_id"],
            "partition": case["partition"],
            "verdict": case["verdict"],
            "target": case["verdict"] in POSITIVE_VERDICTS,
            "coherence": float(coherence),
        })

    development = [row for row in rows if row["partition"] == "development"]
    positives = [row for row in development if row["target"]]
    negatives = [row for row in development if not row["target"]]
    if len(positives) < 4 or len(negatives) < 4:
        decision = "refused_insufficient_development_labels"
        reason = "Нужно не менее четырёх предварительных примеров каждого класса."
        threshold = None
        best = None
    else:
        values = sorted({row["coherence"] for row in development})
        candidates = [values[0] - 1e-12]
        candidates += [(left + right) / 2 for left, right in zip(values, values[1:])]
        candidates.append(values[-1] + 1e-12)
        scored = [(*_balanced_accuracy(development, value), value) for value in candidates]
        best_accuracy, best_errors, best_value = max(
            scored, key=lambda item: (item[0], -item[1], item[2])
        )
        best = {
            "threshold": best_value,
            "balanced_accuracy": round(best_accuracy, 6),
            "classification_errors": best_errors,
        }
        max_negative = max(row["coherence"] for row in negatives)
        min_positive = min(row["coherence"] for row in positives)
        margin = min_positive - max_negative
        if best_errors == 0 and margin >= 0.01:
            threshold = (max_negative + min_positive) / 2
            decision = "calibrated_on_preliminary_development_only"
            reason = "Development-классы разделены без ошибок с запасом не менее 0.01."
        else:
            threshold = None
            decision = "refused_non_separable_development_labels"
            reason = (
                "Средняя cosine-связность не отделяет предварительно цельные линии "
                "от смешанных; production-порог не выбран."
            )

    holdout_rows = [row for row in rows if row["partition"] == "internal_holdout"]
    if threshold is None:
        holdout = {
            "status": "not_run_without_development_threshold",
            "case_count": len(holdout_rows),
            "metrics_exposed_to_threshold_selection": False,
        }
    else:
        accuracy = sum((row["coherence"] >= threshold) == row["target"]
                       for row in holdout_rows) / len(holdout_rows)
        holdout = {
            "status": "internal_sanity_check_only",
            "case_count": len(holdout_rows),
            "accuracy": round(accuracy, 6),
            "not_independent_scientific_validation": True,
        }
    return {
        "metric": "exact mean pairwise cosine on saved SPECTER2/proximity vectors",
        "development_rows": development,
        "development_class_counts": {
            "positive": len(positives), "negative": len(negatives),
        },
        "observed_development_range": [
            min(row["coherence"] for row in development),
            max(row["coherence"] for row in development),
        ],
        "best_descriptive_cutpoint": best,
        "decision": decision,
        "reason": reason,
        "production_threshold": threshold,
        "existing_experimental_threshold_0_5_status": (
            "not_validated_and_non_discriminating_for_this_sample"
        ),
        "holdout_evaluation": holdout,
    }


def build_primary_annotation_queue(annotations: dict, evidence: dict) -> dict:
    evidence_by_id = {case["candidate_id"]: case for case in evidence["cases"]}
    items = []
    for case in sorted(annotations["cases"], key=lambda value: value["candidate_id"]):
        if case["partition"] != "development":
            continue
        works = evidence_by_id[case["candidate_id"]]["works"]
        indices = sorted({0, len(works) // 2, len(works) - 1})
        for index in indices:
            work = works[index]
            items.append({
                "candidate_id": case["candidate_id"],
                "proposed_line_title": case["proposed_human_title"],
                "work_id": work["work_id"],
                "effective_date": work["effective_date"],
                "title": work["title"],
                "abstract": work.get("abstract"),
                "identifiers": work.get("identifiers", []),
                "selection_role": (
                    "earliest" if index == 0 else
                    "latest" if index == len(works) - 1 else "middle"
                ),
                "primary_result_label": None,
                "rationale": None,
            })
    return {
        "version": (
            "primary-result-annotation-queue-0.4.11"
            if annotations.get("recheck_overlay_version")
            else "primary-result-annotation-queue-0.4.10"
        ),
        "annotation_version": annotations.get("version"),
        "recheck_overlay_version": annotations.get("recheck_overlay_version"),
        "annotation_sha256": digest(annotations),
        "recheck_overlay_sha256": annotations.get("recheck_overlay_sha256"),
        "mission_id": evidence["mission_id"],
        "score_run_id": evidence["score_run_id"],
        "evidence_report_payload_sha256": evidence["report_payload_sha256"],
        "partition": "development_only",
        "selection": "earliest_middle_latest_per_development_candidate",
        "allowed_labels": PRIMARY_LABELS,
        "item_count": len(items),
        "labels_completed": 0,
        "items": items,
        "limitations": [
            "Очередь не содержит автоматически присвоенных содержательных меток.",
            "Название без слова review не доказывает наличие первичного результата.",
            "До ручной проверки G6_primary_sources должен оставаться unknown.",
        ],
    }


def build_report(annotations: dict, evidence: dict, cards: dict,
                 primary_queue: dict) -> dict:
    validation = validate_annotations(annotations, evidence)
    if digest(cards) != evidence.get("score_packet_sha256"):
        raise ValueError("Score packet не совпал с packet, использованным для evidence export.")
    if cards.get("score_run_id") != evidence.get("score_run_id"):
        raise ValueError("Score run различается между cards и evidence.")
    diagnostic = coherence_diagnostic(annotations, cards)
    result = {
        "version": (
            "composition-calibration-0.4.11"
            if annotations.get("recheck_overlay_version")
            else "composition-calibration-0.4.10"
        ),
        "mission_id": evidence["mission_id"],
        "score_run_id": evidence["score_run_id"],
        "bindings": {
            "evidence_report_payload_sha256": validation["payload_sha256"],
            "score_packet_sha256": evidence["score_packet_sha256"],
            "annotation_sha256": digest(annotations),
            "primary_annotation_queue_sha256": digest(primary_queue),
        },
        "annotation_version": annotations.get("version"),
        "recheck_overlay_version": annotations.get("recheck_overlay_version"),
        "validation": validation,
        "coherence_diagnostic": diagnostic,
        "primary_result_calibration": {
            "decision": "not_started_unlabelled_queue_created",
            "production_classifier": None,
            "verified_primary_result_count": None,
            "annotation_queue_items": primary_queue["item_count"],
            "reason": (
                "Проверенных per-paper меток пока нет; title-only эвристика исключена "
                "из доказательств первичности."
            ),
        },
        "detector_or_saved_score_modified": False,
        "scientific_weak_signal_accuracy_evaluated": False,
        "limitations": [
            "Разметка состава выполнена одним внутренним annotator и не является gold standard.",
            "Internal holdout не является независимой слепой научной проверкой.",
            "Composition label не подтверждает зарождение, будущий рост или рынок.",
            "Даже успешная калибровка на 15 кластерах не была бы достаточной для production без внешней разметки.",
        ],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--recheck", type=Path,
                        help="versioned adjudication overlay for the base annotations")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--primary-queue-output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.primary_queue_output.exists():
        parser.error("Выходной файл уже существует; нужен новый версионированный путь.")
    annotations = read_json(args.annotations)
    evidence = read_json(args.evidence)
    cards = read_json(args.cards)
    if args.recheck is not None:
        annotations = apply_recheck(annotations, read_json(args.recheck), evidence)
    validate_annotations(annotations, evidence)
    primary_queue = build_primary_annotation_queue(annotations, evidence)
    report = build_report(annotations, evidence, cards, primary_queue)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.primary_queue_output.parent.mkdir(parents=True, exist_ok=True)
    with args.primary_queue_output.open("x", encoding="utf-8") as handle:
        json.dump(primary_queue, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({
        "output": str(args.output),
        "primary_queue_output": str(args.primary_queue_output),
        "coherence_decision": report["coherence_diagnostic"]["decision"],
        "primary_queue_items": primary_queue["item_count"],
        "report_payload_sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
