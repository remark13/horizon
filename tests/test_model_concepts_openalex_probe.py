from datetime import date
import json

from saia.discovery import DiscoveryResult, Publication
from scripts.probe_model_concepts_openalex import run


def test_unreviewed_model_probe_preserves_limits_and_literal_postfilter(tmp_path):
    path = tmp_path / "proposals.json"
    path.write_text(json.dumps({
        "version": "ru-concept-plan-diagnostic-v2",
        "rows": [{
            "case_id": "q", "role": "outside_priority_map",
            "query_ru": "Квантовые сенсоры для археологии",
            "status": "parsed",
            "proposal_unreviewed": {
                "concept_groups": [
                    {"alternatives_en": ["quantum sensors"]},
                    {"alternatives_en": ["archaeology"]},
                ],
                "needs_human_review": False, "ambiguities_ru": [],
            },
            "structural_validation": {
                "issues": [], "index_plan_shape_supported": True,
            },
        }],
    }), encoding="utf-8")

    def discover(query, start, cutoff, limit):
        assert query == '("quantum sensors") AND ("archaeology")'
        assert (start, cutoff, limit) == (
            date(2021, 9, 1), date(2026, 9, 1), 25)
        return DiscoveryResult(
            query=query, date_from=start.isoformat(),
            as_of_date=cutoff.isoformat(), query_hash="test",
            fetched_at="2026-09-25T00:00:00Z",
            works=(Publication(
                canonical_key="doi:test", title="Quantum sensors for archaeology",
                abstract="A field survey", published_at="2025-01-01",
                sources=("openalex",), source_ids=("W1",),
                urls=("https://doi.org/test",), doi="test", authors=(),
            ),), source_counts={"openalex": 1}, errors={},
        )

    result = run(path, date_from=date(2021, 9, 1),
                 as_of_date=date(2026, 9, 1), discover=discover)
    row = result["rows"][0]
    assert row["status"] == "first_page_observed"
    assert row["literal_concept_matches_first_page"] == 1
    assert result["limitations"]["results_are_not_weak_signals"] is True
