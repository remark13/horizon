from copy import deepcopy

import pytest

from saia.evidence_comparison import compare


def report(external=False):
    gates = [
        {"gate": "G0_concentration", "passed": None},
        {"gate": "G0_independent_orgs", "passed": None},
        {"gate": "G3_independent_teams", "passed": None},
    ]
    value = {
        "snapshot_id": "snapshot", "snapshot_content_sha256": "snapshot-sha",
        "input_text_hash": "text", "methodology_hash": "method",
        "measurement_policy_hash": "measure", "policy_hash": "policy",
        "assessment_content_sha256": "enriched" if external else "baseline",
        "candidates": [{
            "candidate_id": "one", "work_ids": [1, 2],
            "channels": ["lexical"], "composition_sha256": "composition",
            "assessment": {"status": "candidate", "gates": gates},
            "observed": {"independent_teams_proxy": None,
                         "independent_orgs_proxy": None,
                         "author_identity_conflict_works": 0},
        }],
    }
    if external:
        value["external_evidence_provenance"] = {
            "match_rule": "exact_arxiv_identifier_only",
            "candidate_membership_changed": False,
        }
    return value


def test_evidence_comparison_counts_known_identity_without_claiming_accuracy():
    before, after = report(), report(True)
    for gate in after["candidates"][0]["assessment"]["gates"]:
        gate["passed"] = gate["gate"] != "G0_independent_orgs"
    after["candidates"][0]["observed"].update(
        independent_teams_proxy=2,
        independent_orgs_proxy=1,
        author_identity_conflict_works=1,
    )
    result = compare(before, after)
    assert result["same_exact_compositions"] is True
    assert result["changed_status_count"] == 0
    assert result["identity_gate_outcomes"]["G3_independent_teams"] == {
        "baseline_unknown": 1, "enriched_unknown": 0,
        "enriched_passed": 1, "enriched_failed": 0,
    }
    assert result["candidate_evidence_coverage"] == {
        "known_team_proxy": 1,
        "known_organisation_proxy": 1,
        "with_author_identity_conflict": 1,
    }
    assert result["precision_measured"] is False


@pytest.mark.parametrize("change", ["snapshot", "candidate", "composition", "membership"])
def test_evidence_comparison_rejects_nonisolated_changes(change):
    before, after = report(), report(True)
    if change == "snapshot":
        after["snapshot_id"] = "other"
    elif change == "candidate":
        after["candidates"][0]["candidate_id"] = "other"
    elif change == "composition":
        after["candidates"][0]["work_ids"] = [1, 3]
    else:
        after["external_evidence_provenance"]["candidate_membership_changed"] = True
    with pytest.raises(ValueError):
        compare(before, after)


def test_evidence_comparison_does_not_mutate_reports():
    before, after = report(), report(True)
    frozen = deepcopy((before, after))
    compare(before, after)
    assert (before, after) == frozen
