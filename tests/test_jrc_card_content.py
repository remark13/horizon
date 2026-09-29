from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from saia import jrc_card_content as jrc, public_signals, scout_public_signals
from saia.api import app
from saia.hybrid import digest
from saia.public_signal_brief import render
from saia.scout_web import SCOUT_WEB_HTML


def test_curated_content_is_bound_to_source_titles_and_pages():
    value = jrc.load()
    assert len(value["records"]) == 39
    assert len({row["id"] for row in value["records"]}) == 39
    assert sum(bool(row.get("historical_radar")) for row in value["records"]) == 7
    jrc.validate(value, public_signals.load_catalog())


@pytest.mark.local_data
def test_curated_content_matches_optional_local_source_pdf():
    source = Path(__file__).resolve().parents[1] / "data/reference/jrc/JRC140959-weak-signals-2024.pdf"
    if not source.is_file():
        pytest.skip("The source PDF is an optional local reference and is not bundled with the public source package.")
    value = jrc.load()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == value["source_pdf_sha256"]


@pytest.mark.parametrize("change", ["wrong_title", "wrong_page", "duplicate", "confidence", "bad_metric", "no_ru", "bad_period"])
def test_bad_or_unbound_content_fails_closed(change):
    value = deepcopy(jrc.load()); row = value["records"][0]
    if change == "wrong_title": row["title"] = "another technology"
    elif change == "wrong_page": row["pages"] = [120]
    elif change == "duplicate": value["records"].append(deepcopy(row))
    elif change == "confidence": row["confidence"] = 99
    elif change == "no_ru": del row["what"]["ru"]
    elif change == "bad_period": row["pages"] = [True]
    else: row["historical_radar"] = {"page": 37, "activity_percent": float("nan"), "publications_percent": 70, "patents_percent": 30, "persistence_years": 6}
    with pytest.raises(ValueError): jrc.validate(value, public_signals.load_catalog())


def test_side_content_does_not_change_reference_identity_or_source_catalogue():
    catalogue = deepcopy(public_signals.load_catalog())
    reference = scout_public_signals.lookup("jrc-2024-p113-041")
    identifiers = {row["id"] for row in reference["references"]}
    originals = [row for row in catalogue["records"] if row["id"] in identifiers]
    expected = digest({"catalog_version": public_signals.VERSION,
                       "records": sorted(originals, key=lambda row: row["id"])})
    assert reference["reference_content_sha256"] == expected
    explanation = reference["source_explanations"][0]
    assert explanation["used_for_score"] is False
    assert explanation["primary_papers_verified"] is False
    assert explanation["current_weak_signal_verified"] is False
    assert reference["scientific_score"] is None
    assert explanation["explanation_payload_sha256"] == digest({key: value for key, value in explanation.items()
                                                                if key not in {"html", "explanation_payload_sha256"}})
    explanation["actors"].clear()
    assert len(scout_public_signals.lookup("jrc-2024-p113-041")["source_explanations"][0]["actors"]) == 5
    assert public_signals.load_catalog() == catalogue


def test_unmapped_or_archived_records_do_not_receive_invented_description():
    catalogue = public_signals.load_catalog()["records"]
    assert jrc.for_record(next(row for row in catalogue if row["source_id"] == "jrc_weak_signals_2021")) is None
    assert jrc.for_record(next(row for row in catalogue if row["id"] == "jrc-2024-p106-001")) is None
    row = deepcopy(next(row for row in catalogue if row["id"] == "jrc-2024-p113-041"))
    row["title"] = "Different title"
    assert jrc.for_record(row) is None


def test_historical_metrics_keep_units_and_period_conflict_not_a_new_score():
    reference = scout_public_signals.lookup("jrc-2024-p120-072")
    content = reference["source_explanations"][0]
    assert content["historical_radar"] == {"page": 37, "activity_percent": 95.56, "publications_percent": 79.03,
                                           "patents_percent": 20.97, "persistence_years": 6}
    assert content["activity_period_consistent"] is False
    html = render(reference)
    assert "95,56%" in html and "6 лет" in html
    assert "2021–2024" in html and "2021–2023" in html
    assert "не вероятность" in html and "не текущий" in html


def test_content_and_export_are_bilingual_cited_and_escaped_without_scripts():
    reference = scout_public_signals.lookup("jrc-2024-p113-041")
    russian, english = render(reference, "ru"), render(reference, "en")
    assert "Что сообщает JRC" in russian and "Что меняется" in russian
    assert "University of Macau" in russian and "#page=113" in russian
    assert 'lang="en">Joint use of UAVs' in english
    assert "CC BY 4.0" in english and "GPT-4-32K" in english
    content = deepcopy(reference["source_explanations"][0])
    content["what"]["ru"] = '<script>alert("noise")</script>'
    content["actors"][0] = '<img src=x onerror=alert(1)>'
    assert "<script" not in jrc.render(content) and "<img" not in jrc.render(content)
    assert "<script" not in russian and "<script" not in english


def test_catalogue_api_and_scout_have_same_cited_content():
    client = TestClient(app)
    row = client.get("/api/public-signals?source_id=jrc_weak_signals_2024&query=edge+computing+for+UAV").json()["records"][0]
    reference = scout_public_signals.lookup(row["id"])
    assert row["source_explanations"] == reference["source_explanations"]
    page = client.get("/public-signals").text
    assert 'id="public-drawer" role="dialog"' in page and 'data-public-detail=' in page
    assert "publicExplanationHtml(r)" in page and "publicExplanationHtml(r)" in SCOUT_WEB_HTML
    assert "document.querySelector('main').inert=true" in page
    assert "e.key==='Escape'" in page


def test_public_content_is_never_a_detector_metric_or_training_label():
    reference = scout_public_signals.match("drone", included_phrases=[])
    assert reference["scientific_results_modified"] is False and reference["not_gold_labels"] is True
    assert reference["model_called"] is False and reference["network_requested"] is False
    assert all(row["scientific_score"] is None for row in reference["records"])
