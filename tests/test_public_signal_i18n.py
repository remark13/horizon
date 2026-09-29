from copy import deepcopy
import hashlib
import json
import re

from fastapi.testclient import TestClient
import pytest

from saia.api import app
from saia import public_signal_i18n as i18n
from saia import public_signals
from saia import public_signal_brief
from saia.public_signal_content_web import PUBLIC_CONTENT_SCRIPT, _script_json
from saia.scout_public_signals import lookup, match
from saia.scout_public_signals_web import PUBLIC_STYLE

client = TestClient(app)


def test_every_catalogue_title_and_category_has_russian_content_including_archive():
    rows = public_signals.load_catalog()["records"]
    assert len(rows) == 454
    assert all(re.search("[А-Яа-яЁё]", i18n.title_ru(row) or "") for row in rows)
    assert all(i18n.category_ru(row["category"]) for row in rows)
    assert sum(public_signals.is_current(row) for row in rows) == 361
    translations = i18n.load_translations()
    assert translations["catalog_version"] == public_signals.VERSION
    assert set(translations["titles"]) <= {row["title"] for row in rows}


def test_presentation_is_a_copy_and_cannot_rewrite_canonical_source_records():
    catalog = public_signals.load_catalog()
    before = deepcopy(catalog)
    file_before = hashlib.sha256(public_signals.CATALOG_PATH.read_bytes()).hexdigest()
    row = next(row for row in catalog["records"] if row["id"] == "jrc-2024-p106-001")
    display = i18n.present(row)
    assert display["title"] == row["title"] == "anion exchange membrane electrolyzer"
    assert display["title_ru"] == "Электролизёр с анионообменной мембраной"
    assert display["category_ru"] == "Передовое производство и новые материалы"
    assert display["id"] == row["id"] and display["source_url"] == row["source_url"]
    assert display["source_date"] == row["source_date"]
    assert catalog == before and "title_ru" not in row
    assert hashlib.sha256(public_signals.CATALOG_PATH.read_bytes()).hexdigest() == file_before


def test_bilingual_search_uses_the_same_canonical_id_and_accepts_e_yo():
    identifiers = lambda query: {row["id"] for row in public_signals.search(
        query=query, source_id="jrc_weak_signals_2024", limit=100)["records"]}
    assert identifiers("анионообменной") == identifiers("anion exchange") == {"jrc-2024-p106-001"}
    assert identifiers("электролизер") == identifiers("электролизёр")
    assert "espas-2026-p073" in {row["id"] for row in public_signals.search(query="биороботы")["records"]}
    assert public_signals.search(query="квантовые", limit=100)["total"] > 0
    result = public_signals.search(limit=100)
    assert result["content_languages"] == ["ru", "en"]
    assert result["default_content_language"] == "ru"
    assert result["content_translation_version"] == i18n.VERSION


def test_translation_does_not_change_scout_matching_or_public_reference_hash():
    result = match("беспилотные авиационные системы")
    assert result["total"] == 3
    reference = lookup("jrc-2024-p113-041")
    frozen = deepcopy(reference)
    digest = reference["reference_content_sha256"]
    assert i18n.title(reference) == "Периферийные вычисления для беспилотников"
    assert i18n.title(reference, "en") == "edge computing for UAV"
    public_signal_brief.render(reference, "ru")
    public_signal_brief.render(reference, "en")
    assert reference == frozen and reference["reference_content_sha256"] == digest
    assert "title_ru" not in reference


@pytest.mark.parametrize("lang,title", [
    ("ru", "Периферийные вычисления для беспилотников"),
    ("en", "edge computing for UAV"),
])
def test_download_has_selected_content_language_but_russian_interface(lang, title):
    response = client.get("/public-signals/jrc-2024-p113-041/brief", params={"lang": lang})
    assert response.status_code == 200
    assert f'<h1 lang="{lang}">{title}</h1>' in response.text
    assert "Из публичного источника" in response.text and "Открыть первоисточник" in response.text
    assert "#page=113" in response.text and "<script" not in response.text
    assert f"-{lang}.html" in response.headers["content-disposition"]


@pytest.mark.parametrize("value", ["de", "RU", "", "../en", '<script>alert(1)</script>'])
def test_unsupported_language_is_rejected(value):
    assert client.get("/public-signals/jrc-2024-p113-041/brief", params={"lang": value}).status_code == 422
    with pytest.raises(ValueError, match="ru и en"):
        i18n.title({"title": "x"}, value)


def test_historical_snapshot_gets_display_translation_without_mutation():
    reference = lookup("jrc-2021-p14-001")
    snapshot = deepcopy(reference)
    snapshot.pop("title_ru", None)
    for source in snapshot["references"]:
        source.pop("title_ru", None)
        source.pop("source_year", None)
    before = deepcopy(snapshot)
    assert "Производство в атомном масштабе" in public_signal_brief.render(snapshot)
    assert "Архивная подборка до 2023 года" in public_signal_brief.render(snapshot)
    assert snapshot == before


def test_original_case_typos_and_source_links_are_not_silently_rewritten():
    reference = lookup("espas-2026-p020")
    assert reference["title"] == "REVERSAL OF CHORINC DISEASES"
    assert i18n.title(reference) == "Обращение вспять хронических заболеваний"
    assert i18n.title(reference, "en") == reference["title"]
    assert "digital signature" in i18n.load_translations()["notes"]["kyber digital signature algorithm"]
    assert i18n.title({"title": "ENDINEERING"}) == "Проектирование завершения использования продуктов"
    assert i18n.title({"title": "WOMBFARE"}) == "Рождаемость как политическое оружие"


def test_unknown_future_term_is_not_given_a_fictional_translation():
    assert i18n.title({"title": "Unknown new title"}) == "Unknown new title"
    assert i18n.category("Untranslated category") == "Untranslated category"


def test_content_controls_are_shared_and_do_not_reload_or_rebuild_forms():
    for url in ("/public-signals", "/scout"):
        html = client.get(url).text
        assert 'aria-label="Язык содержимого"' in html
        assert 'data-public-content-language="ru"' in html and 'data-public-content-language="en"' in html
        assert "saia.public-content-language.v1" in html
        assert "try{localStorage" in html and "window.addEventListener('storage'" in html
    assert "syncPublicContentLanguage()" in PUBLIC_CONTENT_SCRIPT
    assert "fetch(" not in PUBLIC_CONTENT_SCRIPT and "location.reload" not in PUBLIC_CONTENT_SCRIPT
    assert "innerHTML" not in PUBLIC_CONTENT_SCRIPT
    assert "bindPublicContentLanguage" not in PUBLIC_STYLE
    assert "Object.hasOwn" in PUBLIC_CONTENT_SCRIPT
    assert "publicContentStorageBound" in PUBLIC_CONTENT_SCRIPT
    from saia.scout_public_signals_web import PUBLIC_SCRIPT
    assert "${publicContentControlMarkup}<h2" in PUBLIC_SCRIPT
    assert PUBLIC_SCRIPT.rstrip().endswith("bindPublicContentLanguage();")


def test_script_json_cannot_close_an_inline_script_and_duplicate_keys_are_rejected():
    unsafe = "</script><img src=x onerror=alert(1)>\u2028\u2029"
    encoded = _script_json({"title": unsafe})
    assert "<" not in encoded and "</script>" not in encoded
    assert json.loads(encoded)["title"] == unsafe
    with pytest.raises(ValueError, match="Повтор ключа"):
        i18n._unique_object([("x", "a"), ("x", "b")])
