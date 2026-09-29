"""Controlled internal profiles for validation of SAIA across priority areas.

Profiles are not UI choices and may not silently narrow a user's free query.
They support vocabulary work, source routing and cross-domain evaluation; they
are not labels, ontologies or proof that a technology is a weak signal.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

import yaml


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "focus-areas.v0.4.32.yaml"
EXPECTED_IDS = {
    "artificial-intelligence",
    "new-materials-and-chemistry",
    "production-and-automation",
    "nuclear-and-energy",
    "transport-mobility",
    "unmanned-aircraft-systems",
    "food-security",
    "health-preservation",
    "multi-satellite-constellations",
}
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _normalised(value: str) -> str:
    return " ".join(value.casefold().split())


def _validate(payload: dict) -> dict:
    if payload.get("version") != "focus-areas-0.4.32":
        raise ValueError("Unsupported focus-area catalog version")
    profiles = payload.get("profiles")
    policy = payload.get("policy") or {}
    if policy.get("ui_exposed_as_fixed_choices") is not False:
        raise ValueError("Focus-area profiles must not constrain the universal UI")
    if policy.get("user_query_may_be_narrowed_automatically") is not False:
        raise ValueError("User queries must not be narrowed automatically")
    if not isinstance(profiles, list) or len(profiles) != len(EXPECTED_IDS):
        raise ValueError("Focus-area catalog must contain exactly nine profiles")
    ids = [profile.get("id") for profile in profiles]
    if set(ids) != EXPECTED_IDS or len(ids) != len(set(ids)):
        raise ValueError("Focus-area profile identifiers are incomplete or duplicated")
    for profile in profiles:
        if not _ID.fullmatch(profile["id"]):
            raise ValueError(f"Invalid focus-area identifier: {profile['id']}")
        required = (
            "name_ru", "short_name_ru", "role", "mission_ru",
            "starter_query_en", "aliases_ru", "aliases_en",
            "exclusions_en", "enrichment_sources", "source_note_ru", "subareas",
        )
        missing = [key for key in required if not profile.get(key)]
        if missing:
            raise ValueError(f"{profile['id']} misses required fields: {', '.join(missing)}")
        if len(profile["subareas"]) < 5:
            raise ValueError(f"{profile['id']} has too few subareas")
        if any(set(item) != {"id", "name_ru", "query_en"} for item in profile["subareas"]):
            raise ValueError(f"{profile['id']} has malformed subarea fields")
        sub_ids = [item.get("id") for item in profile["subareas"]]
        if len(sub_ids) != len(set(sub_ids)) or any(not _ID.fullmatch(value or "") for value in sub_ids):
            raise ValueError(f"{profile['id']} has invalid or duplicate subarea identifiers")
        queries = [_normalised(item.get("query_en", "")) for item in profile["subareas"]]
        if any(len(query) < 4 for query in queries) or len(queries) != len(set(queries)):
            raise ValueError(f"{profile['id']} has empty or duplicate subarea queries")
    return payload


@lru_cache(maxsize=1)
def load() -> dict:
    return _validate(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


def catalog() -> dict:
    payload = load()
    return {
        "version": payload["version"],
        "status": payload["status"],
        "policy": payload["policy"],
        "count": len(payload["profiles"]),
        "profiles": payload["profiles"],
        "interpretation": (
            "Профили используются внутри для словарей, маршрутизации источников и "
            "межотраслевой проверки. Они не являются вариантами пользовательского "
            "интерфейса, разметкой или доказательством тренда."
        ),
    }


def evaluation_matrix() -> tuple[dict, ...]:
    """Return an unlabelled internal matrix; never present it as a benchmark."""
    cases = []
    for item in catalog()["profiles"]:
        cases.append({
            "case_id": f"{item['id']}/broad",
            "profile_id": item["id"],
            "scope": "broad",
            "query_en": item["starter_query_en"],
            "ground_truth_status": "unlabelled",
            "manual_review_required": True,
        })
        cases.extend({
            "case_id": f"{item['id']}/{subarea['id']}",
            "profile_id": item["id"],
            "scope": "subarea",
            "query_en": subarea["query_en"],
            "ground_truth_status": "unlabelled",
            "manual_review_required": True,
        } for subarea in item["subareas"])
    return tuple(cases)


def profile(profile_id: str) -> dict:
    for item in catalog()["profiles"]:
        if item["id"] == profile_id:
            return item
    raise ValueError("Unknown focus-area profile")
