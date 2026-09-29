from saia.openalex_work_variants import collapse


TITLE = "A symmetric thermal architecture for subsea desalination"


def _row(identifier, day, authors=None, title=TITLE):
    return {"openalex_id": identifier, "publication_date": day,
            "title": title, "authors": authors}


def test_exact_long_title_and_same_author_collapses_versions_but_keeps_ids():
    rows = [{**_row("W2", "2026-06-01", ["Jennifer Hagberg"]),
             "source_mission_ids": ["new"]},
            {**_row("W1", "2026-04-20", ["Jennifer Hagberg"]),
             "source_mission_ids": ["old"]},
            {**_row("W3", "2026-06-01", ["Jennifer Hagberg"]),
             "source_mission_ids": ["new"]}]
    result, audit = collapse(rows)
    assert len(result) == 1
    assert result[0]["openalex_id"] == "W1"
    assert result[0]["source_openalex_variant_ids"] == ["W1", "W2", "W3"]
    assert result[0]["source_mission_ids"] == ["new", "old"]
    assert audit["variant_rows_collapsed"] == 2
    assert audit["families_with_multiple_ids"] == 1


def test_no_merge_for_different_authors_missing_authors_short_title_or_long_gap():
    rows = [_row("W1", "2025-01-01", ["Author A"]),
            _row("W2", "2025-02-01", ["Author B"]),
            _row("W3", "2025-02-01", []),
            _row("W4", "2025-03-01", []),
            _row("W5", "2025-02-01", ["Author A"], "Nuclear energy"),
            _row("W6", "2025-03-01", ["Author A"], "Nuclear energy"),
            _row("W7", "2026-02-01", ["Author A"])]
    result, audit = collapse(rows)
    assert len(result) == len(rows)
    assert audit["variant_rows_collapsed"] == 0


def test_duplicate_input_openalex_id_is_rejected():
    rows = [_row("W1", "2025-01-01", ["Author A"]),
            _row("W1", "2025-02-01", ["Author A"])]
    try:
        collapse(rows)
    except ValueError as error:
        assert "Duplicate" in str(error)
    else:
        raise AssertionError("Duplicate source ID accepted")
