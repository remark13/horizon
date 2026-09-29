from copy import deepcopy
from datetime import date

import pytest

from saia import publication_profile as p


def card():
    return {"metrics": {"observed": {"doc_count": 25}, "publication_series": {
        "as_of_date": "2026-09-01", "coverage_comparable": None, "points": [
            {"start": f"{year}-01-01", "end": f"{year+1}-01-01", "topic_works": count, "complete": True}
            for year, count in [(2021, 3), (2022, 2), (2023, 4), (2024, 6), (2025, 8)]
        ] + [{"start": "2026-01-01", "end": "2026-09-01", "topic_works": 2, "complete": False}]}}}


def test_activity_is_sample_fraction_not_growth_or_scoring():
    c = card(); before = deepcopy(c)
    result = p.build(c, today=date(2026, 9, 29))
    assert result["recent_years"] == [2023, 2025]
    assert (result["recent_works"], result["eligible_works"]) == (18, 23)
    assert result["recent_share_percent"] == pytest.approx(78.26)
    assert result["found_works"] == 25 and result["used_for_score"] is False
    assert "18 из 23 работ до конца 2025" in p.render(result)
    assert result["coverage_comparable"] is None
    assert result["active_period_from"] == "2021-01-01"
    assert "2021–2026" in p.render(result) and "не вероятность" in p.render(result)
    assert c == before


@pytest.mark.parametrize("mutation", ["gap", "overlap", "missing_end", "bool_count", "partial_year", "future", "zero", "split_boundary"])
def test_invalid_or_incomplete_window_does_not_produce_percent(mutation):
    c = card(); points = c["metrics"]["publication_series"]["points"]
    if mutation == "gap":
        points.pop(3)
    elif mutation == "overlap":
        points[3]["start"] = "2023-12-01"
    elif mutation == "missing_end":
        del points[3]["end"]
    elif mutation == "bool_count":
        points[3]["topic_works"] = True
    elif mutation == "partial_year":
        points[3]["complete"] = False
    elif mutation == "future":
        points[-1]["end"] = "2027-01-01"
    elif mutation == "split_boundary":
        points[1]["end"] = points[2]["start"] = "2023-02-01"
    else:
        for row in points: row["topic_works"] = 0
    assert p.build(c, today=date(2026, 9, 29))["recent_share_percent"] is None


def test_short_history_and_tiny_sample_stay_explicit():
    c = card(); points = c["metrics"]["publication_series"]["points"]
    c["metrics"]["publication_series"]["points"] = points[3:]
    assert p.build(c, today=date(2026, 9, 29))["recent_share_percent"] is None
    c = card()
    for row in c["metrics"]["publication_series"]["points"]: row["topic_works"] = 1
    result = p.build(c, today=date(2026, 9, 29))
    assert result["small_sample"] is True and "Малая выборка" in p.render(result)


def test_historical_snapshot_uses_its_own_end_year_not_today():
    c = card(); c["metrics"]["publication_series"]["as_of_date"] = "2024-01-01"
    c["metrics"]["publication_series"]["points"] = c["metrics"]["publication_series"]["points"][:3]
    result = p.build(c, today=date(2026, 9, 29))
    assert result["recent_years"] == [2021, 2023] and result["recent_share_percent"] == 100
