"""Query-matched published foresight references, separate from detector output.

The public catalogue is never used to generate, score or label scientific
candidates. This read model joins it only to the scout's presentation layer.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import re

from saia import public_signals, query_planning
from saia.focus_areas import catalog as focus_catalog
from saia.hybrid import digest
from saia.scientific_aliases import regular_plural_key

VERSION = "scout-public-reference-0.4.58"
BADGE = "Из публичного источника"
STOP = frozenset("and or the for of in to a an technology technologies new advanced system systems model models".split())
# Equivalence of spellings/abbreviations, not a list of expected signals.
ALIASES = {
    "unmanned aerial vehicles": "drone", "unmanned aerial vehicle": "drone",
    "unmanned aircraft systems": "drone", "unmanned aircraft system": "drone",
    "unmanned aviation systems": "drone", "uavs": "drone", "uav": "drone", "uas": "drone",
    "artificial intelligence": "ai", "large language models": "llm", "large language model": "llm",
}
BROAD_CATEGORIES = {
    "artificial-intelligence": {"AI and Machine Learning"},
    "new-materials-and-chemistry": {"Materials", "Advanced Manufacturing and advanced materials"},
    "nuclear-and-energy": {"Energy"},
    "transport-mobility": {"Mobility and Transport", "Aerospace"},
    "health-preservation": {"Therapeutics and Biotechnologies", "Medicine and Biotechnology", "e-Health", "Medical Imaging"},
}


def words(value: str) -> tuple[str, ...]:
    normal = " ".join(re.findall(r"[a-zа-яё0-9]+", str(value).casefold()))
    for original, replacement in ALIASES.items():
        normal = re.sub(r"(?<!\w)" + re.escape(original) + r"(?!\w)", replacement, normal)
    return tuple(regular_plural_key(word) for word in normal.split() if word not in STOP)


def title_key(value: str) -> tuple[str, ...]:
    """Only lexical duplicates; never merge by one shared subject word."""
    return tuple(regular_plural_key(word) for word in re.findall(r"[a-zа-яё0-9]+", str(value).casefold()))


def parse_ids(value: str | None, *, maximum: int = 100) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Неверный список опубликованных сигналов.")
    parts = value.split(",")
    if (not 1 <= len(parts) <= maximum or len(set(parts)) != len(parts)
            or any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", part) or len(part) > 100 for part in parts)):
        raise ValueError("Неверный список опубликованных сигналов.")
    return parts


def reference(row: dict, members: list[dict] | None = None) -> dict:
    members = members or [row]
    value = deepcopy(row)
    value.update({
        "catalog_version": public_signals.VERSION, "origin": "public_foresight",
        "source_badge": BADGE, "scientific_score": None, "expert_validated": False,
        "references": [{**deepcopy(member), "catalog_version": public_signals.VERSION} for member in members],
        "reference_content_sha256": digest({"catalog_version": public_signals.VERSION, "records": sorted(members, key=lambda item: item["id"])}),
        "date_semantics": "Год подборки / дата выпуска источника, не дата возникновения технологии",
        "archived": (public_signals.edition_year(row) or 0) < public_signals.MINIMUM_ACTIVE_YEAR,
    })
    # Add the report-side explanation after hashing the canonical reference.
    # Frozen dispatches, title matching and scientific scoring stay unchanged.
    from saia.jrc_card_content import for_record
    explanations = [content for member in members if (content := for_record(member))]
    if explanations:
        value["source_explanations"] = explanations
    return value


def lookup(identifier: str) -> dict:
    parse_ids(identifier, maximum=1)
    records = public_signals.load_catalog()["records"]
    row = next((item for item in records if item["id"] == identifier), None)
    if row is None:
        raise ValueError("Опубликованный сигнал отсутствует в закреплённом каталоге.")
    archived = row["source_year"] < public_signals.MINIMUM_ACTIVE_YEAR
    members = sorted((item for item in records if title_key(item["title"]) == title_key(row["title"])
                      and (item["source_year"] < public_signals.MINIMUM_ACTIVE_YEAR) == archived),
                     key=lambda item: (item["source_date"], item["id"]), reverse=True)
    return reference(row, members)


def _broad_profiles(query: str) -> list[dict]:
    normal = query_planning._normalise(query)
    return [profile for profile in focus_catalog()["profiles"]
            if normal in {query_planning._normalise(value) for value in
                          [profile["name_ru"], profile["short_name_ru"], profile["starter_query_en"],
                           *profile["aliases_ru"], *profile["aliases_en"]]}]


def match(query: str, *, included_phrases: list[str] | None = None,
          excluded_phrases: list[str] | None = None, limit: int = 100,
          today: date | None = None) -> dict:
    """Bounded lexical match using the query and its controlled search vocabulary.

Category matches are allowed only for an exact broad direction, never for a
narrower request containing its name. Relevance is not signal confidence.
"""
    if not isinstance(query, str) or len(query) > 500 or not 1 <= limit <= 100:
        raise ValueError("Неверный запрос к опубликованным сигналам.")
    query = " ".join(query.split())
    today = today or datetime.now(timezone.utc).date()
    phrases = [query, *(included_phrases or [])]
    broad = _broad_profiles(query) if query else []
    categories = set()
    for profile in broad:
        phrases.extend(profile["aliases_en"])
        categories.update(BROAD_CATEGORIES.get(profile["id"], set()))
    # Use suggestions only when no saved compiled phrase list was provided.
    # A saved selection must not be expanded again to all branches of an area.
    if query and included_phrases is None:
        plan = query_planning.preview(query)
        phrases.extend(phrase for item in plan["suggestions"] for phrase in
                       item.get("phrases_en") or [item["query_en"]])
    needles = [(phrase, set(words(phrase))) for phrase in dict.fromkeys(phrases) if phrase]
    needles = [(phrase, tokens) for phrase, tokens in needles if tokens]
    exclusions = [set(words(phrase)) for phrase in excluded_phrases or [] if words(phrase)]
    grouped: dict[tuple[str, ...], list[dict]] = {}
    for row in public_signals.load_catalog()["records"]:
        if not public_signals.is_current(row, today=today):
            continue
        grouped.setdefault(title_key(row["title"]), []).append(row)
    found = []
    for members in grouped.values():
        members.sort(key=lambda row: (row["source_date"], row["id"]), reverse=True)
        row = members[0]
        tokens = set(words(row["title"]))
        ru_tokens = set(words(row.get("title_ru", "")))
        if any(exclusion <= tokens or exclusion <= ru_tokens for exclusion in exclusions):
            continue
        matched = [phrase for phrase, needle in needles if needle <= tokens or needle <= ru_tokens]
        category_matches = sorted({item["category"] for item in members} & categories)
        if not matched and not category_matches:
            continue
        value = reference(row, members)
        value["matched_phrases"] = matched[:10]
        value["matching_basis"] = "title_terms" if matched else "source_category_for_exact_broad_query"
        value["matching_explanation"] = (
            "Название содержит поисковые понятия: " + "; ".join(matched[:3]) + "."
            if matched else "Тематический раздел каталога «" + category_matches[0] + "» соответствует широкому запросу."
        )
        value["current_weak_signal_verified"] = False
        found.append(value)
    found.sort(key=lambda row: (row["matching_basis"] == "title_terms", row["source_date"], row["id"]), reverse=True)
    for index, row in enumerate(found, 1):
        row["reference_rank"] = index
    return {"version": VERSION, "catalog_version": public_signals.VERSION, "query": query,
            "total": len(found), "records": found[:limit], "limit": limit,
            "matching_policy": "controlled_query_title_terms_or_exact_broad_source_category",
            "not_gold_labels": True, "scientific_results_modified": False,
            "network_requested": False, "model_called": False,
            "minimum_active_year": public_signals.MINIMUM_ACTIVE_YEAR,
            "temporal_scope": "published_catalog_available_today_not_historical_detector_evidence"}


def scope_from_payload(payload: dict, terms: list[str] | None = None,
                       exclusions: list[str] | None = None) -> dict:
    plan = payload.get("controlled_search_plan") or {}
    query = plan.get("original_query") or payload.get("original_query") or (terms or [""])[0]
    return {"query": query, "included_phrases": list(plan.get("included_terms") or terms or []),
            "excluded_phrases": list(plan.get("exclusions") or exclusions or [])}


def for_packet(packet: dict) -> dict:
    """Read the exact query version of this result, never the newest mission query."""
    provenance = packet.get("provenance") or []
    query_id = next((row.get("query_version_id") for row in provenance if row.get("query_version_id")), None)
    if query_id is None:
        return match("")
    from saia import db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT terms,exclusions,payload FROM query_version WHERE query_version_id=%s AND mission_id=%s",
                    (query_id, packet["mission_id"]))
        row = cur.fetchone()
    if row is None:
        raise ValueError("Сохранённая версия запроса для внешних подборок не найдена.")
    result = match(**scope_from_payload(row[2], row[0], row[1]))
    result["query_version_id"] = query_id
    return result


def select(packet: dict, identifiers: list[str] | None = None) -> tuple[dict, list[dict]]:
    references = for_packet(packet)
    if identifiers is None:
        return references, references["records"]
    if not isinstance(identifiers, list) or any(not isinstance(value, str) for value in identifiers):
        raise ValueError("Неверный список опубликованных сигналов.")
    if not identifiers:
        return references, []
    parse_ids(",".join(identifiers))
    found = {row["id"]: row for row in references["records"]}
    if any(identifier not in found for identifier in identifiers):
        raise ValueError("Опубликованный сигнал не относится к выдаче выбранного запроса.")
    return references, [found[identifier] for identifier in identifiers]


def for_card(mission: str, score: int, card: dict) -> list[dict]:
    if not card.get("label"):
        return []
    from saia import db
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT q.terms,q.exclusions,q.payload FROM analysis_run r "
                    "JOIN query_version q ON q.query_version_id=r.query_version_id "
                    "WHERE r.run_id=%s AND r.mission_id=%s AND r.kind='score' AND r.status='done'",
                    (score, mission))
        row = cur.fetchone()
    if row is None:
        raise ValueError("Сохранённый запрос карточки не найден.")
    found = match(**scope_from_payload(row[2], row[0], row[1]))
    key = title_key(card["label"])
    return [reference for reference in found["records"] if title_key(reference["title"]) == key]


def join(result: dict, references: dict) -> dict:
    """Separate types, one paged UI list. Scientific queue/ranks remain intact."""
    queue = result.get("queue") or []
    keys = {title_key(item["card"]["label"]): item for item in queue}
    additions = []
    for row in references["records"]:
        duplicate = keys.get(title_key(row["title"]))
        if duplicate is not None:
            duplicate.setdefault("published_references", []).append(row)
        else:
            additions.append({"item_kind": "public_signal", "public_signal": row,
                              "assessment": None, "rank": None})
    result["public_signals"] = references
    result["result_items"] = [{**item, "item_kind": "saia_candidate"} for item in queue] + additions
    result["result_counts"] = {"saia_candidates": len(queue), "public_signals": len(additions),
                               "public_references_attached_to_candidates": len(references["records"]) - len(additions),
                               "total": len(result["result_items"])}
    result["public_signal_policy"] = {"badge": BADGE, "scientific_score_assigned": False,
                                      "scientific_queue_changed": False,
                                      "order": "scientific_candidates_then_query_matched_published_references",
                                      "duplicates": "identical_normalised_titles_only_keep_all_citations"}
    return result
