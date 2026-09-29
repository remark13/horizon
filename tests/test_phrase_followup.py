from pathlib import Path

import pytest

from saia.phrase_followup import run


def test_followup_rejects_mismatched_discovery_window(monkeypatch):
    monkeypatch.setattr("saia.phrase_followup.sha256_file", lambda path: "index-hash")
    config = {"discovery_report_sha256": "report-hash",
              "index_manifest_sha256": "index-hash", "date_from": "2016-09-01",
              "as_of_date_exclusive": "2026-09-01", "max_matches_per_branch": 5000,
              "branches": []}
    report = {"parent_collection": {"index_manifest_sha256": "index-hash"},
              "version": "broad-title-phrase-proposals-v3",
              "period": {"date_from": "2017-09-01",
                         "as_of_date_exclusive": "2026-09-01"}}
    with pytest.raises(ValueError, match="Unpinned"):
        run(config=config, report=report, config_sha256="config-hash",
            report_sha256="report-hash", index_dir=Path("ignored"))


def test_followup_counts_unique_years_but_never_confirms_signal(monkeypatch):
    monkeypatch.setattr("saia.phrase_followup.sha256_file", lambda path: "index-hash")
    monkeypatch.setattr("saia.phrase_followup.exact_search", lambda index, plan: {
        "arxiv_ids": ["a", "b"], "audit": {"exact_unique_ids": 2}})
    monkeypatch.setattr("saia.phrase_followup._materialize", lambda index, ids: {
        "a": {"arxiv_id": "a", "first_submission_date": "2024-10-01"},
        "b": {"arxiv_id": "b", "first_submission_date": "2026-02-01"}})
    config = {"discovery_report_sha256": "report-hash",
              "index_manifest_sha256": "index-hash", "date_from": "2016-09-01",
              "as_of_date_exclusive": "2026-09-01", "matching_version": "orthographic-separators-v1",
              "max_matches_per_branch": 5000, "limitations": [],
              "branches": [{"id": "phrase", "role": "diagnostic", "kind": "or_phrase",
                            "included_terms": ["world action model"]}]}
    report = {"parent_collection": {"index_manifest_sha256": "index-hash"},
              "version": "broad-title-phrase-proposals-v3",
              "period": {"date_from": "2016-09-01",
                         "as_of_date_exclusive": "2026-09-01"}}
    result = run(config=config, report=report, config_sha256="config-hash",
                 report_sha256="report-hash", index_dir=Path("ignored"))
    assert result["branches"][0]["annual_current_metadata_matches"] == {
        "2024": 1, "2026": 1}
    assert result["branches"][0]["first_matching_work"] == "a"
    assert result["weak_signal_confirmed"] is False
