from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from saia.controlled_collection import sha256_file
from saia.priority_arxiv_materialize import VERSION
from saia.priority_arxiv_pilot import _sha


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/processed/priority-arxiv-pilot-corpus-v1"


@pytest.mark.local_data
def test_saved_corpus_matches_frozen_full_scan() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    full = ROOT / "data/processed/priority-arxiv-full-v1/report.json"
    report = json.loads(full.read_text())
    assert manifest["version"] == VERSION
    assert manifest["source"]["full_scan_report_sha256"] == _sha(full)
    assert manifest["scanned_mirror_rows"] == report["scanned_rows"] == 3_164_528
    assert manifest["eligible_unique_documents"] == 63_718
    assert manifest["case_assignments"] == 63_732
    assert manifest["assignment_counts"] == {
        row["catalog_id"]: row["eligible_unique_in_pinned_mirror"]
        for row in report["cases"] if row["eligible_unique_in_pinned_mirror"] > 0
    }
    for name, recorded in manifest["files"].items():
        path = CORPUS / name
        assert path.stat().st_size == recorded["bytes"]
        assert sha256_file(path) == recorded["sha256"]
    assert pq.ParquetFile(CORPUS / "documents.parquet").metadata.num_rows == 63_718
    assert pq.ParquetFile(CORPUS / "assignments.parquet").metadata.num_rows == 63_732


def test_corpus_does_not_promote_literal_matches_to_signal_labels() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    limits = manifest["interpretation"]
    assert limits["case_memberships_are_relevance_labels"] is False
    assert limits["case_memberships_are_weak_signal_labels"] is False
    assert limits["field_denominator_available"] is False
    assert limits["historical_title_abstract_frozen_at_first_submission"] is False
    assert manifest["rights"]["full_text_pdfs_included"] is False
