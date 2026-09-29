"""Portable unit checks; the full 3.16M-row integration run is a separate artifact."""

from scripts.build_robotic_narrow_line_temporal import _paper, _share, _window_year


def test_september_august_windows_and_zero_denominator():
    assert _window_year("2025-08-31") == 2024
    assert _window_year("2025-09-01") == 2025
    assert _share(1, 4) == 0.25
    assert _share(0, 0) is None


def test_evidence_link_keeps_first_submission_date():
    paper = _paper({
        "arxiv_id": "2503.16806", "first_submission_date": "2025-03-21",
        "title": "DyWA", "categories": "cs.RO cs.AI",
    })
    assert paper == {
        "arxiv_id": "2503.16806", "url": "https://arxiv.org/abs/2503.16806",
        "first_submission_date": "2025-03-21", "title": "DyWA",
        "categories": "cs.RO cs.AI",
    }
