"""Offline display translations; canonical titles and source hashes stay intact."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

VERSION = "public-signal-content-ru-v1"
LANGUAGES = ("ru", "en")
TRANSLATIONS_PATH = (Path(__file__).resolve().parents[1]
                     / "data/reference/public_signals/translations.ru.v1.json")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Повтор ключа в словаре переводов публичных сигналов.")
        value[key] = item
    return value


@lru_cache(maxsize=1)
def load_translations() -> dict:
    value = json.loads(TRANSLATIONS_PATH.read_text(encoding="utf-8"),
                       object_pairs_hook=_unique_object)
    if value.get("version") != VERSION:
        raise ValueError("Неверная версия переводов публичных сигналов.")
    for field in ("titles", "categories", "notes"):
        if not isinstance(value.get(field), dict) or any(
            not isinstance(key, str) or not key.strip()
            or not isinstance(text, str) or not text.strip()
            for key, text in value[field].items()
        ):
            raise ValueError("Словарь переводов публичных сигналов повреждён.")
    return value


def language(value: str) -> str:
    if value not in LANGUAGES:
        raise ValueError("Доступны языки содержимого ru и en.")
    return value


def title_ru(row: dict) -> str | None:
    return row.get("title_ru") or load_translations()["titles"].get(row.get("title", ""))


def category_ru(value: str) -> str | None:
    return load_translations()["categories"].get(value)


def title(row: dict, lang: str = "ru") -> str:
    return (title_ru(row) or row["title"]) if language(lang) == "ru" else row["title"]


def category(value: str, lang: str = "ru") -> str:
    return (category_ru(value) or value) if language(lang) == "ru" else value


def present(row: dict) -> dict:
    """Return a copy. Never translate IDs, lookup keys, source URLs or dates."""
    from saia.jrc_card_content import for_record
    value = {**row, "title_ru": title_ru(row), "category_ru": category_ru(row["category"]),
            "title_translation_version": VERSION,
            "translation_note_ru": load_translations()["notes"].get(row["title"])}
    explanation = for_record(row)
    if explanation:
        value["source_explanations"] = [explanation]
    return value
