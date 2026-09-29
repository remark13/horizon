from saia.bas_narrow_temporal import _later_snapshot_update, _share, _window_year
from scripts.build_bas_narrow_directness_packet import _stable_pick


def test_september_august_windows_are_exact():
    assert _window_year("2025-08-31") == 2024
    assert _window_year("2025-09-01") == 2025
    assert _window_year("2026-08-31") == 2025


def test_missing_denominator_is_not_zero_share():
    assert _share(0, 0) is None
    assert _share(1, 4) == 0.25


def test_blind_article_selection_does_not_depend_on_input_order():
    rows = [{"arxiv_id": str(index)} for index in range(10)]
    assert _stable_pick(rows, "frozen", 4) == _stable_pick(rows[::-1], "frozen", 4)


def test_later_snapshot_update_is_risk_not_historical_proof():
    assert _later_snapshot_update({"snapshot_update_date": None}, "2025-09-01") is None
    assert _later_snapshot_update({"snapshot_update_date": "2025-08-31T00:00:00"},
                                  "2025-09-01") is False
    assert _later_snapshot_update({"snapshot_update_date": "2025-09-01T00:00:00"},
                                  "2025-09-01") is True
