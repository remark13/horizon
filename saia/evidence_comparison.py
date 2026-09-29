"""Compare the same exact compositions before and after external evidence.

This is an evidence-completeness experiment, not a corpus, model or threshold
comparison. Candidate memberships must be byte-for-byte equivalent in meaning.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from saia import hybrid, runs


VERSION = "exact-composition-evidence-comparison-0.4.20"
IDENTITY_GATES = (
    "G0_concentration",
    "G0_independent_orgs",
    "G3_independent_teams",
)


def _index(report: dict) -> dict[str, dict]:
    rows = {row["candidate_id"]: row for row in report["candidates"]}
    if len(rows) != len(report["candidates"]):
        raise ValueError("Повторён candidate_id.")
    return rows


def _gate(row: dict, name: str):
    matches = [gate["passed"] for gate in row["assessment"]["gates"]
               if gate["gate"] == name]
    if len(matches) != 1:
        raise ValueError("Каждая проверка должна встретиться ровно один раз: " + name)
    return matches[0]


def compare(baseline: dict, enriched: dict) -> dict:
    for key in (
        "snapshot_id", "snapshot_content_sha256", "input_text_hash",
        "methodology_hash", "measurement_policy_hash", "policy_hash",
    ):
        if baseline.get(key) != enriched.get(key):
            raise ValueError("Сравнивать evidence можно только на одном снимке и паспортах: " + key)
    if baseline.get("external_evidence_provenance") is not None:
        raise ValueError("Baseline уже содержит внешний evidence.")
    external = enriched.get("external_evidence_provenance")
    if not external or external.get("match_rule") != "exact_arxiv_identifier_only":
        raise ValueError("Enriched-оценка не содержит допустимый внешний evidence.")
    if external.get("candidate_membership_changed") is not False:
        raise ValueError("Внешний evidence не должен менять состав кандидатов.")
    left, right = _index(baseline), _index(enriched)
    if left.keys() != right.keys():
        raise ValueError("Наборы кандидатов различаются.")
    transitions = Counter()
    status_transitions = Counter()
    gate_summary = {}
    for identifier in left:
        before, after = left[identifier], right[identifier]
        if (before["work_ids"] != after["work_ids"]
                or before["channels"] != after["channels"]
                or before["composition_sha256"] != after["composition_sha256"]):
            raise ValueError("Точный состав кандидата изменился.")
        old_status = before["assessment"]["status"]
        new_status = after["assessment"]["status"]
        if old_status != new_status:
            status_transitions[f"{old_status} -> {new_status}"] += 1
        for gate in IDENTITY_GATES:
            old, new = _gate(before, gate), _gate(after, gate)
            transitions[f"{gate}:{old} -> {new}"] += old != new
    for gate in IDENTITY_GATES:
        gate_summary[gate] = {
            "baseline_unknown": sum(_gate(row, gate) is None for row in left.values()),
            "enriched_unknown": sum(_gate(row, gate) is None for row in right.values()),
            "enriched_passed": sum(_gate(row, gate) is True for row in right.values()),
            "enriched_failed": sum(_gate(row, gate) is False for row in right.values()),
        }
    result = {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": baseline["snapshot_id"],
        "same_exact_compositions": True,
        "candidate_count": len(left),
        "baseline_assessment_sha256": baseline["assessment_content_sha256"],
        "enriched_assessment_sha256": enriched["assessment_content_sha256"],
        "external_evidence_provenance": external,
        "status_transitions": dict(sorted(status_transitions.items())),
        "changed_status_count": sum(status_transitions.values()),
        "identity_gate_outcomes": gate_summary,
        "changed_identity_gate_outcomes": {
            key: value for key, value in sorted(transitions.items()) if value
        },
        "candidate_evidence_coverage": {
            "known_team_proxy": sum(
                row["observed"].get("independent_teams_proxy") is not None
                for row in right.values()
            ),
            "known_organisation_proxy": sum(
                row["observed"].get("independent_orgs_proxy") is not None
                for row in right.values()
            ),
            "with_author_identity_conflict": sum(
                (row["observed"].get("author_identity_conflict_works") or 0) > 0
                for row in right.values()
            ),
        },
        "precision_measured": False,
        "weak_signal_detection_measured": False,
        "production_thresholds_changed": False,
        "limitations": [
            "OpenAlex связан по уже известному arXiv ID и не является независимым discovery-каналом.",
            "Авторские ID и организации являются текущими библиографическими proxy, а не доказательством исторической независимости лабораторий.",
            "Сохранение или изменение статуса не измеряет precision, recall, рынок или будущий успех.",
        ],
        "code_version": runs.code_version(),
    }
    result["comparison_payload_sha256"] = hybrid.digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--enriched", type=Path, required=True)
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    if args.export.exists():
        parser.error("Сравнение уже существует; старые результаты не перезаписываются.")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    enriched = json.loads(args.enriched.read_text(encoding="utf-8"))
    result = compare(baseline, enriched)
    args.export.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "candidate_count": result["candidate_count"],
        "changed_status_count": result["changed_status_count"],
        "identity_gate_outcomes": result["identity_gate_outcomes"],
        "sha256": result["comparison_payload_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
