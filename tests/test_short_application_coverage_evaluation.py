from pathlib import Path

from scripts.evaluate_short_application_coverage import evaluate


def test_frozen_coverage_audit_keeps_relational_false_positive_visible():
    root = Path(__file__).resolve().parents[1]
    report = evaluate(
        root / "outputs/free-ru-short-application-first15-review-packet-2026-09-27-v1.json",
        root / "config/free-ru-short-application-metadata-review-2026-09-27-v1.json",
        root / "config/free-ru-short-application-coverage-groups-2026-09-27-v1.json",
    )
    assert len(report["rows"]) == 45
    collisions = [row for row in report["rows"]
                  if row["machine_all_groups_observed"]
                  and not row["developer_metadata_observed"]]
    assert [(row["case_id"], row["rank"]) for row in collisions] == [
        ("national-area-039", 13)]
    assert report["developer_terms_not_independent"]
