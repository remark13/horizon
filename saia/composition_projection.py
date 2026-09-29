"""Development-only sensitivity of publication dynamics to composition QA.

The projection removes only explicitly reviewed topical outliers and collapses
only explicitly declared research families.  It does not split mixed clusters,
does not inspect holdout cases and never assigns a weak-signal status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from saia.composition_calibration import (
    POSITIVE_VERDICTS,
    apply_recheck,
    evidence_payload_sha256,
    read_json,
)
from saia.hybrid import digest
from saia.measurement import WindowCounts, analyze_series


def _work_date(work: dict) -> date:
    raw = work.get("effective_date") or work.get("publication_date")
    if not raw:
        raise ValueError("Для projection у каждой работы нужна дата.")
    return date.fromisoformat(raw)


def _counts_by_window(points: list[dict], works: list[dict]) -> list[int]:
    dates = [_work_date(work) for work in works]
    counts = []
    assigned = 0
    for point in points:
        start = date.fromisoformat(point["start"])
        end = date.fromisoformat(point["end"])
        value = sum(start <= value_date < end for value_date in dates)
        counts.append(value)
        assigned += value
    if assigned != len(dates):
        raise ValueError("Работы projection выходят за сохранённые календарные окна.")
    return counts


def _series(points: list[dict], works: list[dict], as_of: date) -> dict:
    counts = _counts_by_window(points, works)
    windows = [
        WindowCounts(
            start=date.fromisoformat(point["start"]),
            end=date.fromisoformat(point["end"]),
            topic_works=count,
            corpus_works=point.get("corpus_works"),
            complete=point.get("complete", True),
            coverage_comparable=point.get("coverage_comparable"),
        )
        for point, count in zip(points, counts)
    ]
    return analyze_series(windows, as_of)


def _collapse_families(works: list[dict], families: list[list[int]]) -> tuple[list[dict], list[dict]]:
    by_id = {work["work_id"]: work for work in works}
    if len(by_id) != len(works):
        raise ValueError("Work ID в projection должны быть уникальны.")
    used = set()
    collapsed = []
    findings = []
    for family in families:
        members = list(family)
        present = [work_id for work_id in members if work_id in by_id]
        if len(present) < 2:
            continue
        if used.intersection(present):
            raise ValueError("Research family groups пересекаются.")
        used.update(present)
        representative = min((by_id[work_id] for work_id in present), key=_work_date)
        collapsed.append(representative)
        findings.append({
            "work_ids": sorted(present),
            "counted_once_at": _work_date(representative).isoformat(),
            "representative_work_id": representative["work_id"],
        })
    collapsed.extend(work for work in works if work["work_id"] not in used)
    collapsed.sort(key=lambda work: work["work_id"])
    return collapsed, findings


def _summary(series: dict) -> dict:
    observed = series["observed_sample"]
    return {
        "direction": series["direction"],
        "absolute_count_change": series["absolute_count_change"],
        "share_change": series["share_change"],
        "share_slope_per_window": series["share_slope_per_window"],
        "consecutive_active_full_windows": series["consecutive_active_full_windows"],
        "last_required_window_counts": [
            point["topic_works"]
            for point in series["points"][-series["required_history_windows"]:]
        ],
        "observed_count_direction": observed["count"]["direction"],
        "observed_count_slope_per_window": observed["count"]["slope_per_window"],
        "scientific_signal_confirmed": observed["scientific_signal_confirmed"],
    }


def build_projection(annotations: dict, current_evidence: dict,
                     cards: dict) -> dict:
    evidence_payload_sha256(current_evidence)
    if digest(cards) != current_evidence.get("score_packet_sha256"):
        raise ValueError("Current cards не совпали с current evidence packet.")
    evidence_by_composition = {
        case["composition_sha256"]: case for case in current_evidence["cases"]
    }
    cards_by_composition = {
        card["composition_sha256"]: card for card in cards["cards"]
    }
    families = {}
    for finding in annotations.get("research_family_findings", []):
        families.setdefault(finding["composition_sha256"], []).append(
            finding["work_ids"]
        )
    selected = [
        case for case in annotations["cases"]
        if case["partition"] == "development"
        and case["verdict"] in POSITIVE_VERDICTS
    ]
    rows = []
    for case in sorted(selected, key=lambda value: value["composition_sha256"]):
        composition = case["composition_sha256"]
        if composition not in evidence_by_composition or composition not in cards_by_composition:
            raise ValueError("Projection composition отсутствует в current artifacts.")
        evidence = evidence_by_composition[composition]
        card = cards_by_composition[composition]
        works = sorted(evidence["works"], key=lambda work: work["work_id"])
        work_ids = {work["work_id"] for work in works}
        excluded = set(case["outlier_work_ids"])
        if not excluded <= work_ids:
            raise ValueError("Projection outlier отсутствует в точном составе.")
        retained = [work for work in works if work["work_id"] not in excluded]
        independent, family_actions = _collapse_families(
            retained, families.get(composition, [])
        )
        original = card["metrics"]["publication_series"]
        points = original["points"]
        raw_counts = _counts_by_window(points, works)
        saved_counts = [point["topic_works"] for point in points]
        if raw_counts != saved_counts:
            raise ValueError("Evidence dates не воспроизводят сохранённый raw series.")
        as_of = date.fromisoformat(original["as_of_date"])
        raw_series = _series(points, works, as_of)
        topical_series = _series(points, retained, as_of)
        independent_series = _series(points, independent, as_of)
        rows.append({
            "composition_sha256": composition,
            "origin_candidate_id": case["candidate_id"],
            "current_candidate_id": evidence["candidate_id"],
            "topic_id": evidence["topic_id"],
            "title": case["proposed_human_title"],
            "verdict": case["verdict"],
            "raw_work_count": len(works),
            "topical_core_work_count": len(retained),
            "independent_family_proxy_count": len(independent),
            "excluded_outlier_work_ids": sorted(excluded),
            "family_collapse_actions": family_actions,
            "raw": _summary(raw_series),
            "topical_core": _summary(topical_series),
            "independent_family_proxy": _summary(independent_series),
            "status_recomputed": False,
            "weak_signal_truth_evaluated": False,
        })
    development_total = sum(
        case["partition"] == "development" for case in annotations["cases"]
    )
    result = {
        "version": "composition-publication-projection-0.4.11",
        "scope": "development_positive_compositions_only",
        "annotation_version": annotations.get("version"),
        "recheck_overlay_version": annotations.get("recheck_overlay_version"),
        "mission_id": cards["mission_id"],
        "score_run_id": cards["score_run_id"],
        "bindings": {
            "annotation_sha256": digest(annotations),
            "recheck_overlay_sha256": annotations.get("recheck_overlay_sha256"),
            "current_evidence_payload_sha256": current_evidence["report_payload_sha256"],
            "current_score_packet_sha256": digest(cards),
        },
        "counts": {
            "development_compositions": development_total,
            "projected_positive_compositions": len(rows),
            "development_split_or_reject_not_projected": development_total - len(rows),
            "holdout_compositions_loaded": 0,
        },
        "rows": rows,
        "aggregate": {
            "raw_works": sum(row["raw_work_count"] for row in rows),
            "topical_core_works": sum(row["topical_core_work_count"] for row in rows),
            "independent_family_proxy": sum(
                row["independent_family_proxy_count"] for row in rows
            ),
            "directions_changed_after_topical_qa": sum(
                row["raw"]["direction"] != row["topical_core"]["direction"]
                for row in rows
            ),
            "directions_changed_after_family_proxy": sum(
                row["topical_core"]["direction"]
                != row["independent_family_proxy"]["direction"]
                for row in rows
            ),
        },
        "decision": {
            "score_status_recomputed": False,
            "production_rule_changed": False,
            "holdout_evaluated": False,
            "reason": (
                "Это sensitivity-анализ публикационной динамики после ручного QA "
                "состава. Split/reject кейсы нельзя пересчитать без явной per-work "
                "разметки подлиний; weak-signal статус из этого отчёта не выводится."
            ),
        },
        "limitations": [
            "Topical outlier decisions are preliminary single-annotator judgements.",
            "Research-family collapse is a declared manual proxy, not entity resolution.",
            "Publication growth after composition QA is not weak-signal ground truth.",
            "No internal holdout content, vectors or metrics are loaded.",
        ],
    }
    result["report_payload_sha256"] = digest(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--recheck", type=Path, required=True)
    parser.add_argument("--label-evidence", type=Path, required=True)
    parser.add_argument("--cards", type=Path, required=True)
    parser.add_argument("--current-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Выходной файл уже существует; нужен новый версионированный путь.")
    base = read_json(args.annotations)
    label_evidence = read_json(args.label_evidence)
    annotations = apply_recheck(base, read_json(args.recheck), label_evidence)
    current_evidence = read_json(args.current_evidence)
    cards = read_json(args.cards)
    report = build_projection(annotations, current_evidence, cards)
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
        "projected": report["counts"]["projected_positive_compositions"],
        "holdout_loaded": report["counts"]["holdout_compositions_loaded"],
        "aggregate": report["aggregate"],
        "report_payload_sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
