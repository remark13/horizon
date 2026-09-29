from fastapi.testclient import TestClient
import pytest

from saia.api import app
from saia.public_signals import load_catalog, search


client = TestClient(app)


def test_pinned_catalog_keeps_external_sources_separate_from_own_score():
    catalog = load_catalog()
    assert catalog["version"] == "public-signals-catalog-v6"
    assert len(catalog["records"]) == 454  # includes the preserved 93-item archive
    assert {source["source_id"]: source["extracted_total"] for source in catalog["source_documents"]} == {
        "jrc_weak_signals_2024": 216,
        "espas_signal_cards_2026": 92,
        "jrc_weak_signals_2021": 93,
        "business_finland_signals_2026": 5,
        "business_finland_signals_2025": 3,
        "eea_eionet_horizon_scan_2024": 25,
        "wef_emerging_technologies_2026": 10,
        "msit_kribb_biotechnologies_2025": 10,
    }
    assert all((row["source_page"] or row.get("source_section")) and row["source_url"].startswith("https://")
               for row in catalog["records"])
    assert all(row["record_kind"].startswith("published_") for row in catalog["records"])


def test_catalog_search_and_pagination():
    first = search(limit=15)
    second = search(limit=15, offset=15)
    assert first["total"] == 361
    assert len(first["records"]) == 15
    assert not ({row["id"] for row in first["records"]} &
                {row["id"] for row in second["records"]})
    assert first["scientific_score_modified"] is False
    assert first["not_gold_labels"] is True
    assert search(query="UAV", source_id="jrc_weak_signals_2024")["total"] >= 1
    assert search(category="Extreme", source_id="espas_signal_cards_2026")["total"] == 17
    with pytest.raises(ValueError, match="Архивная"):
        search(source_id="jrc_weak_signals_2021")
    assert all(row["source_year"] >= 2023 for row in first["records"])
    assert first["archived_records"] == 93
    assert search(query="not-a-real-tech-phrase")["total"] == 0


@pytest.mark.parametrize("kwargs", [
    {"source_id": "invented"}, {"category": "invented"},
    {"limit": 0}, {"limit": 101}, {"offset": -1},
    {"source_type": "invented"}, {"year": 2022}, {"year": 2019},
])
def test_catalog_rejects_invalid_filters(kwargs):
    with pytest.raises(ValueError):
        search(**kwargs)


def test_public_signals_api_and_separate_screen():
    page = client.get("/public-signals")
    assert page.status_code == 200
    assert "Это не результаты поиска" not in page.text
    assert "Подборки с 2023 года" not in page.text
    assert "/api/public-signals" in page.text
    assert 'href="/public-signals"' in client.get("/scout").text
    result = client.get("/api/public-signals", params={
        "source_id": "jrc_weak_signals_2024", "query": "air mobility",
    })
    assert result.status_code == 200
    assert result.json()["total"] >= 1
    assert all(row["source_id"] == "jrc_weak_signals_2024"
               for row in result.json()["records"])
    assert client.get("/api/public-signals", params={"source_id": "bad"}).status_code == 422


def test_new_editions_are_labelled_and_their_locators_are_not_invented():
    value = search(source_id="wef_emerging_technologies_2026")
    assert value["total"] == 10
    assert value["matched_source_count"] == 1
    world_models = next(row for row in value["records"] if row["id"] == "wef-2026-09")
    assert world_models["source_url"].endswith("/#9-world-models")
    assert all(row["source_type"] == "technology_selection"
               and row["type_label"] == "Перспективная технология"
               and row["source_year"] == 2026
               and row["source_page"] is None and row["source_section"]
               for row in value["records"])
    assert search(source_type="technology_selection", limit=100)["total"] == 20
    assert search(year=2024, limit=100)["total"] == 241
    assert search(query="биороботы", source_id="msit_kribb_biotechnologies_2025")["records"][0]["id"] == "kribb-2025-07"
    assert all(source["edition_year"] >= 2023 for source in value["sources"])


def test_cutoff_uses_edition_year_not_new_upload_or_publication_date():
    from datetime import date
    from saia.public_signals import is_current
    assert not is_current({"source_year": 2022, "source_date": "2025-12-12"}, today=date(2026, 9, 29))
    assert is_current({"source_year": 2023, "source_date": "2024-01-01"}, today=date(2026, 9, 29))
    assert not is_current({"source_year": 2027, "source_date": "2027-01-01"}, today=date(2026, 9, 29))
    assert not is_current({"source_year": 2026, "source_date": "2026-10-01"}, today=date(2026, 9, 29))
    assert not is_current({"source_date": "2026-01-01"}, today=date(2026, 9, 29))


def test_public_ui_has_year_type_and_visible_source_grouping():
    page = client.get("/public-signals").text
    assert 'id="year"' in page and 'id="kind"' in page
    assert "source-group" in page and "Из публичного источника" in page
    assert "source.type_label" in page and "source_page!=null" in page
    assert "controller?.abort()" in page
