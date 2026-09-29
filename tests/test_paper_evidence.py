from datetime import date

from saia.paper_evidence import assess_title, assess_title_v1, assess_works, choose_evidence


def work(identifier, title, day, window="2025", quality=0.8):
    return {
        "work_id": identifier, "title": title, "abstract": "Abstract text",
        "effective_date": date.fromisoformat(day), "window": window,
        "source_quality": quality,
    }


def test_title_diagnostic_does_not_invent_primary_or_verified_relevance():
    old = assess_title(
        "Does a Technique for Building Multimodal Representation Matter?",
        "Active learning — multimodal",
    )
    specific = assess_title(
        "Label What Matters: Multimodal Active Learning",
        "Active learning — multimodal",
    )
    review = assess_title(
        "Multimodal Active Learning: a systematic literature review",
        "Active learning — multimodal",
    )
    assert old["title_match"] == "anchor_not_shown_in_title"
    assert specific["title_match"] == "anchor_present_in_title"
    assert review["format_hint"] == "review_or_synthesis"
    assert all(value["verified_primary_result"] is None for value in (old, specific, review))
    assert all(value["verified_topical_relevance"] is None for value in (old, specific, review))


def test_evidence_selects_earliest_title_supported_non_review_not_oldest_cluster_work():
    works = [
        work(1, "Does a Technique for Building Multimodal Representation Matter?", "2022-06-09"),
        work(2, "Multimodal Active Learning: a systematic review", "2024-01-01"),
        work(3, "Label What Matters: Multimodal Active Learning", "2025-10-01"),
        work(4, "A New Multimodal Active Learning Method", "2026-03-01", window="2026"),
    ]
    diagnostics, summary = assess_works(works, "Active learning — multimodal")
    selected = choose_evidence(works, diagnostics, "2026", 3)
    assert selected[0][0]["work_id"] == 3
    assert selected[0][1] == "ранняя работа с совпадением в названии"
    assert "Первичность исследования" in selected[0][2]
    assert selected[1][0]["work_id"] == 4
    assert summary["total_works"] == 4
    assert summary["verified_primary_results"] is None
    assert summary["title_matches"]["anchor_present_in_title"] == 3


def test_uncheckable_broad_label_keeps_earliest_as_sample_only():
    works = [
        work(1, "Agent Smith: Teaching Question Answering", "2021-12-22"),
        work(2, "AI Agent for Game Testing", "2023-01-01"),
    ]
    diagnostics, _ = assess_works(works, "Agent, game and ai")
    selected = choose_evidence(works, diagnostics, "2025", 2)
    assert selected[0][0]["work_id"] == 1
    assert selected[0][1] == "самая ранняя работа в выборке"
    assert "слишком широко" in selected[0][2]


def test_earliest_explicit_review_is_not_claimed_as_primary_evidence():
    works = [
        work(1, "Photonic Neuromorphic Computing: a mini-review", "2022-01-01"),
        work(2, "Photonic Neuromorphic Computing with Microrings", "2023-01-01"),
    ]
    diagnostics, _ = assess_works(works, "Photonic neuromorphic computing")
    selected = choose_evidence(works, diagnostics, "2025", 2)
    assert selected[0][0]["work_id"] == 2
    assert selected[0][1] == "ранняя работа с совпадением в названии"
    assert diagnostics[1]["format_hint"] == "review_or_synthesis"


def test_duplicate_work_ids_are_not_silently_merged():
    works = [
        work(1, "First", "2022-01-01"),
        work(1, "Second", "2023-01-01"),
    ]
    try:
        assess_works(works, "First method")
    except ValueError as error:
        assert "уникальных" in str(error)
    else:
        raise AssertionError("duplicate ID was accepted")


def test_explicit_consumer_acceptance_title_is_context_not_technical_proof():
    diagnostic = assess_title(
        "Consumer acceptance of cheese alternatives from precision fermentation",
        "Precision fermentation — cheese",
    )
    assert diagnostic["context_hint"] == "acceptance_or_market_context"
    assert diagnostic["verified_primary_result"] is None
    assert diagnostic["verified_topical_relevance"] is None
    assert assess_title("Consumer-grade photonic sensor prototype", "Photonic sensor")[
        "context_hint"
    ] == "unclassified"
    old = assess_title_v1(
        "Consumer acceptance of cheese alternatives from precision fermentation",
        "Precision fermentation — cheese",
    )
    assert old["version"] == "title-evidence-diagnostic-v1"
    assert "context_hint" not in old


def test_technical_example_precedes_market_context_when_both_match_title():
    works = [
        work(1, "Consumer acceptance of precision fermentation protein", "2023-01-01"),
        work(2, "Precision fermentation of recombinant whey protein in yeast", "2024-01-01"),
    ]
    diagnostics, summary = assess_works(works, "Precision fermentation — protein")
    selected = choose_evidence(works, diagnostics, "2025", 2)
    assert selected[0][0]["work_id"] == 2
    assert summary["context_hints"]["acceptance_or_market_context"] == 1
    assert "не прямое свидетельство технического метода" in selected[1][2]
