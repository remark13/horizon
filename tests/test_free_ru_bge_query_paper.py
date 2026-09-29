import json
from pathlib import Path

import pytest

from scripts.probe_free_ru_bge_query_paper import rank_diagnostic


def test_bge_diagnostic_preserves_exact_frozen_records_and_gate():
    root = Path(__file__).resolve().parents[1]
    packet = json.loads((root / "outputs/free-ru-short-application-first15-review-packet-2026-09-27-v1.json").read_text(encoding="utf-8"))
    review = json.loads((root / "config/free-ru-short-application-metadata-review-2026-09-27-v1.json").read_text(encoding="utf-8"))
    scores = [0.0] * len(packet["records"])
    for index, record in enumerate(packet["records"]):
        if record["openalex_id"].endswith("W4387166575"):
            scores[index] = 2.0
        elif record["openalex_id"].endswith("W4212992056"):
            scores[index] = 1.0
    result = rank_diagnostic(packet["records"], scores, review)
    assert result["predeclared_relation_gate_passed"]
    assert result["relation_control"]["topical_review_rank"] == 1
    with pytest.raises(ValueError):
        rank_diagnostic(packet["records"], scores[:-1], review)
