import json
from pathlib import Path

from scripts.probe_cross_domain_structured_line_transfer import selected


ROOT = Path(__file__).resolve().parents[1]


def test_transfer_reads_only_labelled_development_cases():
    config = json.loads((ROOT / "config/cross-domain-structured-line-transfer-v1.json")
                        .read_text(encoding="utf-8"))
    cases, papers = selected(config)
    assert len(cases) == 12
    assert len(papers) == 24
    assert all(case["partition"] == "development" for case in cases)
