"""Versioned policy for bounded external evidence connectors."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "external-sources.v0.4.42.yaml"
VERSION = "external-sources-0.4.42"
PARENT_VERSIONS = {
    "external-sources.v0.4.41.yaml": "external-sources-0.4.41",
    "external-sources.v0.4.40.yaml": "external-sources-0.4.40",
    "external-sources.v0.4.39.yaml": "external-sources-0.4.39",
    "external-sources.v0.4.38.yaml": "external-sources-0.4.38",
    "external-sources.v0.4.37.yaml": "external-sources-0.4.37",
    "external-sources.v0.4.36.yaml": "external-sources-0.4.36",
    "external-sources.v0.4.35.yaml": "external-sources-0.4.35",
    "external-sources.v0.4.34.yaml": "external-sources-0.4.34",
    "external-sources.v0.4.33.yaml": "external-sources-0.4.33",
    "external-sources.v0.4.32.yaml": "external-sources-0.4.32",
    "external-sources.v0.4.31.yaml": "external-sources-0.4.31",
}


def _read_policy(path: Path, seen: set[str]) -> dict:
    if path.name in seen:
        raise ValueError("Цикл наследования политики внешних источников.")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Политика внешних источников должна быть объектом.")
    parent_name = value.get("extends")
    if not parent_name:
        return value
    if parent_name not in PARENT_VERSIONS or Path(parent_name).name != parent_name:
        raise ValueError("Неверная базовая политика внешних источников.")
    parent = _read_policy(path.parent / parent_name, seen | {path.name})
    if parent.get("version") != PARENT_VERSIONS[parent_name]:
        raise ValueError("Неверная версия базовой политики внешних источников.")
    return {**parent, **{key: item for key, item in value.items() if key != "extends"}}


@lru_cache(maxsize=1)
def load_policy() -> dict:
    policy = _read_policy(POLICY_PATH, set())
    if policy.get("version") != VERSION:
        raise ValueError("Нужна точная версия политики внешних источников 0.4.42.")
    if policy.pop("news_publisher_manifest", None) != "news-publishers.v1.json":
        raise ValueError("Не закреплён перечень издателей.")
    from saia.news_publishers import runtime_policy_rows
    policy.update(runtime_policy_rows())
    if policy.get("scientific_score_modified") is not False:
        raise ValueError("Внешние каналы нельзя неявно включать в scientific score.")
    for section in (
        "news", "patents", "funding", "software", "scholar",
        "uk_funding", "eu_programmes", "ai_artifacts", "clinical_trials",
        "europe_pmc", "nasa_ntrs", "usaspending", "mit_news", "nasa_news", "jpl_news",
        "nsf_funding", "datacite_artifacts", "openaire_projects", "osti_records", "nist_news", "aist_news",
        "company_maps", "investment_rounds", "aggregated_news", "media_archive", "aggregated_patents", "google_news",
    ):
        item = policy.get(section)
        if not isinstance(item, dict) or not item.get("source") or not item.get("role"):
            raise ValueError(f"Не заполнена политика внешнего источника: {section}.")
        limit = item.get("max_records", item.get("max_versions", 0))
        if not 1 <= int(limit) <= 100:
            raise ValueError(f"Некорректный лимит внешнего источника: {section}.")
    return policy
