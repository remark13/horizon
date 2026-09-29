"""Pinned public foresight references, never SAIA-generated candidates."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from datetime import date

from saia import public_signal_i18n


CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "data/reference/public_signals/catalog.v6.json"
)
VERSION = "public-signals-catalog-v6"
MINIMUM_ACTIVE_YEAR = 2023
TYPE_LABELS = {
    "weak_signal_report": "Слабый сигнал из отчёта",
    "horizon_scan": "Форсайт-наблюдение",
    "technology_selection": "Перспективная технология",
}


def is_current(row: dict, *, today: date | None = None) -> bool:
    """Edition year, never ingestion time or a refreshed old webpage."""
    today = today or date.today()
    return (isinstance(row.get("source_year"), int)
            and MINIMUM_ACTIVE_YEAR <= row["source_year"] <= today.year
            and str(row.get("source_date", "9999")) <= today.isoformat())


def locator(row: dict) -> str:
    return ("стр. " + str(row["source_page"]) if row.get("source_page") is not None
            else str(row.get("source_section") or "Раздел на сайте"))


def edition_year(row: dict) -> int | None:
    """Also label historical immutable v5 snapshots without changing them."""
    if isinstance(row.get("source_year"), int):
        return row["source_year"]
    document = next((item for item in load_catalog()["source_documents"]
                     if item["source_id"] == row.get("source_id")), None)
    return document["edition_year"] if document else None


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("version") != VERSION:
        raise ValueError("Неверная версия каталога публичных сигналов.")
    documents = catalog.get("source_documents")
    rows = catalog.get("records")
    if not isinstance(documents, list) or not isinstance(rows, list):
        raise ValueError("Каталог публичных сигналов повреждён.")
    sources = {item.get("source_id"): item for item in documents}
    if len(sources) != len(documents) or not sources:
        raise ValueError("Неверные идентификаторы источников каталога.")
    seen = set()
    for item in documents:
        if (not isinstance(item.get("edition_year"), int)
                or item.get("source_type") not in TYPE_LABELS
                or not str(item.get("url", "")).startswith("https://")):
            raise ValueError("Неверный год или тип публичной подборки.")
    for row in rows:
        if (
            not isinstance(row, dict)
            or not row.get("id")
            or row["id"] in seen
            or row.get("source_id") not in sources
            or not isinstance(row.get("title"), str)
            or not row["title"].strip()
            or not ((isinstance(row.get("source_page"), int) and not isinstance(row["source_page"], bool)
                     and row["source_page"] >= 1)
                    or (row.get("source_page") is None and isinstance(row.get("source_section"), str)
                        and row["source_section"].strip()))
            or not str(row.get("source_url", "")).startswith("https://")
            or row.get("source_year") != sources.get(row.get("source_id"), {}).get("edition_year")
            or row.get("source_type") not in TYPE_LABELS
            or row.get("source_type") != sources.get(row.get("source_id"), {}).get("source_type")
            or row.get("type_label") != TYPE_LABELS.get(row.get("source_type"))
        ):
            raise ValueError("Некорректная запись публичного сигнала.")
        seen.add(row["id"])
    for source_id, item in sources.items():
        actual = sum(row["source_id"] == source_id for row in rows)
        if actual != item.get("extracted_total"):
            raise ValueError("Число записей не совпадает с паспортом источника.")
    return catalog


def search(*, query: str = "", source_id: str | None = None,
           category: str | None = None, source_type: str | None = None,
           year: int | None = None, limit: int = 15, offset: int = 0) -> dict:
    catalog = load_catalog()
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ValueError("Некорректная страница каталога.")
    if len(query) > 160:
        raise ValueError("Слишком длинный поисковый запрос.")
    active = [public_signal_i18n.present(row) for row in catalog["records"] if is_current(row)]
    source_ids = {row["source_id"] for row in active}
    documents = [item for item in catalog["source_documents"] if item["source_id"] in source_ids]
    sources = {item["source_id"] for item in catalog["source_documents"]}
    if source_id and source_id not in sources:
        raise ValueError("Неизвестная публичная подборка.")
    if source_id and source_id not in source_ids:
        raise ValueError("Архивная подборка исключена: показываются только издания с 2023 года.")
    if source_type and source_type not in TYPE_LABELS:
        raise ValueError("Неизвестный тип публичной подборки.")
    years = sorted({row["source_year"] for row in active}, reverse=True)
    if year is not None and year not in years:
        raise ValueError("Год отсутствует в актуальном каталоге (издания с 2023 года).")
    categories = {row["category"] for row in active}
    if category and category not in categories:
        raise ValueError("Неизвестная категория публичных сигналов.")
    words = query.casefold().replace("ё", "е").split()
    matched = [
        row for row in active
        if (not source_id or row["source_id"] == source_id)
        and (not category or row["category"] == category)
        and (not source_type or row["source_type"] == source_type)
        and (year is None or row["source_year"] == year)
        and all(word in f"{row['title']} {row.get('title_ru', '')} {row['category']} {row.get('category_ru', '')}".casefold().replace("ё", "е") for word in words)
    ]
    # Keep each provider/edition together within a year, rather than presenting
    # all report types as an undifferentiated ranking of scientific signals.
    order = sorted(documents, key=lambda item: (-item["edition_year"],
                   not bool(item.get("added_at")), item.get("publisher", ""), item["source_id"]))
    source_order = {item["source_id"]: index for index, item in enumerate(order)}
    matched.sort(key=lambda row: (source_order[row["source_id"]], row["id"]))
    return {
        "version": VERSION,
        "total": len(matched),
        "matched_source_count": len({row["source_id"] for row in matched}),
        "limit": limit,
        "offset": offset,
        "records": matched[offset:offset + limit],
        "sources": order,
        "years": years,
        "source_types": [{"value": key, "label": label} for key, label in TYPE_LABELS.items()],
        "minimum_active_year": MINIMUM_ACTIVE_YEAR,
        "archived_records": sum(row["source_year"] < MINIMUM_ACTIVE_YEAR for row in catalog["records"]),
        "link_only_sources": catalog.get("link_only_sources", []),
        "date_semantics": "Год подборки и дата выпуска источника, не дата возникновения технологии",
        "categories": sorted(categories),
        "category_labels_ru": {value: public_signal_i18n.category(value) for value in categories},
        "content_languages": list(public_signal_i18n.LANGUAGES),
        "default_content_language": "ru",
        "content_translation_version": public_signal_i18n.VERSION,
        "role": "external_foresight_reference_only",
        "scientific_score_modified": False,
        "not_gold_labels": True,
        "interpretation": catalog["interpretation"],
    }
