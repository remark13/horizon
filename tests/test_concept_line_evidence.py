from datetime import date
import sqlite3

from scripts.build_concept_line_evidence import _annual, _year_window


def test_september_year_and_background_share() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE works (first_submission_date TEXT, categories TEXT)")
    conn.executemany("INSERT INTO works VALUES (?,?)", [
        ("2024-10-01", "cs.RO"), ("2025-02-01", "cs.RO cs.AI"),
        ("2025-05-01", "cs.AI"), ("2025-10-01", "cs.RO"),
    ])
    assert _year_window("2025-02-01") == 2024
    rows = [{"first_submission_date": "2025-02-01", "categories": "cs.RO"},
            {"first_submission_date": "2025-05-01", "categories": "cs.AI"}]
    annual = _annual(conn, rows, date(2024, 9, 1), date(2026, 9, 1), "cs.RO")
    assert [item["all_exact_matches"] for item in annual] == [2, 0]
    assert [item["line_in_background"] for item in annual] == [1, 0]
    assert [item["background_unique_arxiv_ids"] for item in annual] == [2, 1]
    assert annual[0]["share_per_10000_background"] == 5000
