"""Transparent query suggestions built from controlled internal vocabulary.

The original user query is always preserved. Suggestions are never executed
or treated as translations until a person explicitly confirms them.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import yaml

from saia.focus_areas import catalog


VERSION = "transparent-query-plan-0.4.55"
THEMES_PATH = Path(__file__).resolve().parents[1] / "config" / "query-themes.v0.4.47.yaml"
BRIDGES_PATH = Path(__file__).resolve().parents[1] / "config" / "query-bridges-ru-en.v1.json"


def _normalise(value: str) -> str:
    return " ".join(re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).split())


def _matches(query: str, term: str) -> bool:
    query_n, term_n = _normalise(query), _normalise(term)
    if not query_n or not term_n:
        return False
    if query_n == term_n:
        return True
    # A lone generic AI token in a *specific* request is not evidence that
    # all AI subareas should be searched. Exact broad requests are already
    # handled by query_n == term_n and the controlled theme aliases above.
    # A multi-word controlled phrase may be recognised inside a longer free
    # query. Single generic words are deliberately not used for matching.
    return (
        len(term_n.split()) >= 2 and f" {term_n} " in f" {query_n} "
    ) or (
        len(query_n.split()) >= 2 and len(query_n) >= 7
        and f" {query_n} " in f" {term_n} "
    )


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _themes() -> list[dict]:
    payload = yaml.safe_load(THEMES_PATH.read_text(encoding="utf-8"))
    if payload.get("version") != "query-themes-0.4.47":
        raise ValueError("Неизвестная версия тематического словаря.")
    policy = payload.get("policy") or {}
    if (policy.get("automatic_execution") is not False
            or policy.get("original_query_preserved") is not True
            or policy.get("customer_examples_informed_vocabulary") is not True):
        raise ValueError("Тематический словарь нарушает политику поиска.")
    themes = payload.get("themes") or []
    if not isinstance(themes, list) or not themes:
        raise ValueError("Тематический словарь пуст.")
    identifiers = set()
    for theme in themes:
        identifier = theme.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
            raise ValueError("Некорректный идентификатор направления.")
        if identifier in identifiers:
            raise ValueError("Повтор направления в тематическом словаре.")
        identifiers.add(identifier)
        if not theme.get("name_ru") or not isinstance(theme.get("aliases"), list):
            raise ValueError("Неполная карточка направления.")
        subareas = theme.get("subareas") or []
        if not isinstance(subareas, list) or not 2 <= len(subareas) <= 12:
            raise ValueError("У направления должно быть от 2 до 12 подтем.")
        sub_ids = set()
        for subarea in subareas:
            sub_id, phrases = subarea.get("id"), subarea.get("phrases_en")
            if (not isinstance(sub_id, str)
                    or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", sub_id)
                    or sub_id in sub_ids
                    or not subarea.get("name_ru") or not subarea.get("query_en")
                    or not isinstance(phrases, list) or not 1 <= len(phrases) <= 10
                    or any(not isinstance(phrase, str) or not 2 <= len(phrase) <= 160
                           for phrase in phrases)):
                raise ValueError("Некорректная подтема тематического словаря.")
            sub_ids.add(sub_id)
    return themes


def _matched_theme(query: str) -> dict | None:
    query_n = _normalise(query)
    for theme in _themes():
        for term in [theme["name_ru"], *theme["aliases"]]:
            term_n = _normalise(term)
            # A broad area embedded in a narrower request must not trigger
            # every branch of that area (e.g. AI in medical diagnostics).
            if query_n == term_n:
                return theme
    return None


@lru_cache(maxsize=1)
def _bridges() -> list[dict]:
    payload = json.loads(BRIDGES_PATH.read_text(encoding="utf-8"))
    policy = payload.get("policy") or {}
    if (payload.get("version") != "controlled-exact-ru-en-search-bridges-v1"
            or any(policy.get(key) is not expected for key, expected in {
                "exact_russian_query_only": True,
                "original_query_preserved": True,
                "automatic_execution": False,
                "unknown_query_uses_general_route": True,
                "bridge_is_not_signal_or_relevance_label": True,
            }.items())):
        raise ValueError("Некорректная политика точных поисковых мостов.")
    bridges = payload.get("bridges") or []
    if not 1 <= len(bridges) <= 50:
        raise ValueError("Некорректное число поисковых мостов.")
    identifiers, queries = set(), set()
    for item in bridges:
        identifier = item.get("id")
        variants = item.get("queries_ru")
        phrases = item.get("phrases_en")
        if (not isinstance(identifier, str)
                or not re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", identifier)
                or identifier in identifiers
                or not isinstance(variants, list) or not 1 <= len(variants) <= 5
                or not isinstance(phrases, list) or not 1 <= len(phrases) <= 5
                or any(not isinstance(value, str) or not 2 <= len(value) <= 160
                       for value in variants + phrases)
                or any(not re.fullmatch(r"[a-z0-9 -]+", phrase)
                       for phrase in phrases)
                or not isinstance(item.get("label_ru"), str)
                or not isinstance(item.get("query_en"), str)
                or item["query_en"] not in phrases):
            raise ValueError("Некорректный точный поисковый мост.")
        identifiers.add(identifier)
        for variant in variants:
            key = _normalise(variant)
            if key in queries:
                raise ValueError("Повтор русской формулировки поискового моста.")
            queries.add(key)
    return bridges


def _exact_bridge(query: str) -> dict | None:
    key = _normalise(query)
    return next((item for item in _bridges()
                 if key in {_normalise(value) for value in item["queries_ru"]}), None)


def preview(query: str, max_suggestions: int = 12) -> dict:
    query = " ".join(query.split())
    if not 2 <= len(query) <= 500:
        raise ValueError("Запрос должен содержать от 2 до 500 символов.")
    if not 1 <= max_suggestions <= 20:
        raise ValueError("Число предложений должно быть от 1 до 20.")
    suggestions = []
    matched_profiles = []
    theme = _matched_theme(query)
    if theme:
        matched_profiles.append({
            "profile_id": theme["id"],
            "matched_terms": [query],
            "matched_subarea_ids": [],
            "scientific_sources": ["openalex", "arxiv"],
            "optional_enrichment_sources": [],
            "source_note_ru": "Внутренние поисковые подтемы, не готовый список слабых сигналов.",
        })
        for subarea in theme["subareas"]:
            suggestions.append({
                "suggestion_id": f"{theme['id']}/{subarea['id']}",
                "query_en": subarea["query_en"],
                "label_ru": subarea["name_ru"],
                "phrases_en": subarea["phrases_en"],
                "basis": "controlled_internal_vocabulary",
                "requires_user_confirmation": True,
                "selected": False,
            })
    else:
        matches = []
        for item in catalog()["profiles"]:
            profile_terms = [
                item["name_ru"], item["short_name_ru"], item["starter_query_en"],
                *item["aliases_ru"], *item["aliases_en"],
            ]
            matched_profile_terms = [
                term for term in profile_terms
                if _normalise(query) == _normalise(term)
            ]
            matched_subareas = [
                subarea for subarea in item["subareas"]
                if _matches(query, subarea["name_ru"]) or _matches(query, subarea["query_en"])
            ]
            if matched_profile_terms or matched_subareas:
                matches.append((item, matched_profile_terms, matched_subareas))
        for item, profile_terms, subareas in matches:
            matched_profiles.append({
                "profile_id": item["id"],
                "matched_terms": profile_terms,
                "matched_subarea_ids": [subarea["id"] for subarea in subareas],
                "scientific_sources": ["openalex", "arxiv"],
                "optional_enrichment_sources": item["enrichment_sources"],
                "source_note_ru": item["source_note_ru"],
            })
            proposed = subareas or item["subareas"]
            for subarea in proposed:
                suggestions.append({
                    "suggestion_id": f"{item['id']}/{subarea['id']}",
                    "query_en": subarea["query_en"],
                    "label_ru": subarea["name_ru"],
                    "basis": "controlled_internal_vocabulary",
                    "requires_user_confirmation": True,
                    "selected": False,
                })
        if not suggestions:
            bridge = _exact_bridge(query)
            if bridge:
                matched_profiles.append({
                    "profile_id": bridge["profile_id"],
                    "matched_terms": [query],
                    "matched_subarea_ids": [bridge["id"]],
                    "scientific_sources": ["openalex", "arxiv"],
                    "optional_enrichment_sources": [],
                    "source_note_ru": "Проверенный словарный вариант широкого поиска, не слабый сигнал.",
                })
                suggestions.append({
                    "suggestion_id": bridge["id"],
                    "query_en": bridge["query_en"],
                    "label_ru": bridge["label_ru"],
                    "phrases_en": bridge["phrases_en"],
                    "basis": "controlled_exact_search_bridge",
                    "requires_user_confirmation": True,
                    "selected": False,
                })
    # The compiled API accepts at most 12 branches including the mandatory
    # original query. Never offer more than 11 selectable branches.
    suggestions = suggestions[:min(max_suggestions, 11)]
    payload = {
        "version": VERSION,
        "original_query": query,
        "original_query_preserved": True,
        "automatic_execution": False,
        "automatic_query_replacement": False,
        "matched_profiles": matched_profiles,
        "suggestions": suggestions,
        "status": "suggestions_available" if suggestions else "original_query_only",
        "interpretation": (
            "Это прозрачные предложения из внутреннего словаря. Они не являются "
            "переводом, готовой онтологией или обязательным ограничением поиска."
        ),
        "limitations": [
            "Лексическое совпадение не понимает произвольные синонимы и контекст.",
            "Отсутствие предложения не означает отсутствие научной темы.",
            "Ни одна ветвь не запускается без явного подтверждения пользователя.",
        ],
    }
    payload["plan_payload_sha256"] = _digest(payload)
    return payload
