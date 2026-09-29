from saia.focus_area_diagnostic import summarize


def test_focus_area_smoke_summary_does_not_claim_relevance_or_signal_quality():
    profile = {
        "id": "area",
        "name_ru": "Направление",
        "starter_query_en": "controlled query",
    }
    response = {
        "query_hash": "abc",
        "source_counts": {"openalex": 1, "arxiv": 1},
        "errors": {},
        "limitations": ["bounded"],
        "works": [{
            "title": "Paper", "published_at": "2026-01-01",
            "sources": ["arxiv"], "urls": ["https://arxiv.org/abs/1"],
        }],
    }
    result = summarize(profile, response)
    assert result["canonical_works_in_bounded_preview"] == 1
    assert result["manual_relevance_review_required"] is True
    assert result["weak_signal_assessment_performed"] is False
