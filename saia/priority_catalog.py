"""Versioned source registry for priority search areas and customer examples.

The customer examples are *seed hypotheses*, not independent gold labels.
The national-project rows are *search areas*, not detected weak signals.
Nothing in this module narrows a free-form user query or changes scoring.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from openpyxl import load_workbook


VERSION = "priority-catalog-v1"
CUSTOMER_SHEET = "Слабые сигналы"
DERIVED_SHEET = "Приоритет — 100 сигналов"
AREA_SHEET = "10 направлений — подтемы"
EXPECTED_CUSTOMER_COUNT = 100
EXPECTED_AREA_COUNT = 89
EXPECTED_DIRECTION_COUNT = 10
EXPECTED_CUSTOMER_AREAS = {
    "Индустриальный ИИ", "Инфраструктура ИИ", "Роботы",
    "Финтех", "Защита ИИ", "Edge",
}
DIRECTION_IDS_BY_NAME = {
    "Новые материалы и химия": "new-materials-and-chemistry",
    "Средства производства и автоматизации": "production-and-automation",
    "Новые атомные и энергетические технологии": "nuclear-and-energy",
    "Промышленное обеспечение транспортной мобильности": "transport-mobility",
    "Беспилотные авиационные системы": "unmanned-aircraft-systems",
    "Технологическое обеспечение продовольственной безопасности": "food-security",
    "Новые технологии сбережения здоровья": "health-preservation",
    "Развитие космической деятельности": "space-activity",
    "Экономика данных и цифровая трансформация государства": "data-economy",
    "Искусственный интеллект": "artificial-intelligence",
}

# An area-level, non-exclusive navigation hint, not a relevance label for works.
# Resolve names to IDs from the workbook: row order must not change semantics.
CUSTOMER_DIRECTION_HINTS = {
    "Индустриальный ИИ": ("Средства производства и автоматизации", "Искусственный интеллект"),
    "Инфраструктура ИИ": ("Экономика данных и цифровая трансформация государства", "Искусственный интеллект"),
    "Роботы": ("Средства производства и автоматизации",),
    "Финтех": ("Экономика данных и цифровая трансформация государства",),
    "Защита ИИ": ("Экономика данных и цифровая трансформация государства", "Искусственный интеллект"),
    "Edge": ("Экономика данных и цифровая трансформация государства", "Искусственный интеллект"),
}

URL_RE = re.compile(r"https?://[^\s)\]>]+")


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _value(value) -> str:
    return "" if value is None else str(value)


def _links(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text):
        url = match.rstrip(".,;")
        if urlparse(url).netloc and url not in urls:
            urls.append(url)
    return urls


def _source(path: Path, sheet: str, row: int) -> dict:
    return {"file": path.name, "sha256": _sha256(path), "sheet": sheet, "row": row}


def _number(value, expected: int, sheet: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value != expected:
        raise ValueError(f"{sheet}: expected source number {expected}, got {value!r}")


def build_catalog(customer_path: str | Path, areas_path: str | Path) -> dict:
    """Read the two original workbooks and reject missing or altered client rows."""
    customer_path, areas_path = Path(customer_path), Path(areas_path)
    original = load_workbook(customer_path, read_only=True, data_only=True)
    derived = load_workbook(areas_path, read_only=True, data_only=True)
    try:
        customer_rows = list(original[CUSTOMER_SHEET].values)
        priority_rows = list(derived[DERIVED_SHEET].values)
        area_rows = list(derived[AREA_SHEET].values)
    finally:
        original.close()
        derived.close()
    if len(customer_rows) != EXPECTED_CUSTOMER_COUNT + 2:
        raise ValueError("Original customer workbook must contain exactly 100 examples")
    if len(priority_rows) != EXPECTED_CUSTOMER_COUNT + 1:
        raise ValueError("Derived customer sheet must contain exactly 100 examples")
    if len(area_rows) != EXPECTED_AREA_COUNT + 1:
        raise ValueError("National-project sheet must contain exactly 89 search areas")

    directions = []
    direction_ids = {}
    search_areas = []
    for source_row, row in enumerate(area_rows[1:], start=2):
        number, direction, title, ru_query, en_terms = row[:5]
        _number(number, source_row - 1, AREA_SHEET)
        if not all(isinstance(item, str) and item.strip() for item in (direction, title, ru_query, en_terms)):
            raise ValueError(f"{AREA_SHEET}: incomplete row {source_row}")
        if direction not in direction_ids:
            if direction not in DIRECTION_IDS_BY_NAME:
                raise ValueError(f"Unknown national-project direction: {direction}")
            direction_id = DIRECTION_IDS_BY_NAME[direction]
            direction_ids[direction] = direction_id
            directions.append({"id": direction_id, "name_ru": direction,
                               "role": "technology_direction", "source": _source(areas_path, AREA_SHEET, source_row)})
        search_areas.append({
            "id": f"national-area-{number:03d}", "direction_id": direction_ids[direction],
            "title_ru": title, "role": "search_area_not_signal",
            "query_draft": {"ru": ru_query, "en_terms": [term.strip() for term in en_terms.split(";") if term.strip()],
                            "status": "unreviewed_source_draft"},
            "ipc_draft": _value(row[5]), "investment_terms_draft": _value(row[6]),
            "media_draft": _value(row[7]), "inclusion_rationale_original": _value(row[8]),
            "exclusions": [], "exclusions_status": "not_prepared",
            "hypothesis_type": "unclassified", "classification_status": "not_reviewed",
            "source": _source(areas_path, AREA_SHEET, source_row),
        })
    if len(directions) != EXPECTED_DIRECTION_COUNT or set(direction_ids) != set(DIRECTION_IDS_BY_NAME):
        raise ValueError("National-project sheet must contain exactly 10 directions")
    for names in CUSTOMER_DIRECTION_HINTS.values():
        if not set(names) <= set(direction_ids):
            raise ValueError("Customer-area direction hint does not match source direction names")

    examples = []
    for source_row, (raw, copy) in enumerate(zip(customer_rows[2:], priority_rows[1:]), start=3):
        number = source_row - 2
        _number(raw[1], number, CUSTOMER_SHEET)
        _number(copy[0], number, DERIVED_SHEET)
        # Every client-owned field, including the company text and original URL
        # cell, must survive the derivative copy. The latter has no company
        # column, so it is checked against its preserved source in raw below.
        pairs = ((2, 2), (3, 1), (5, 8), (6, 9), (7, 10), (8, 11), (9, 12))
        mismatches = [f"raw[{a}]/derived[{b}]" for a, b in pairs if raw[a] != copy[b]]
        if mismatches:
            raise ValueError(f"Client example {number} changed in derivative: {', '.join(mismatches)}")
        area = raw[3]
        if area not in EXPECTED_CUSTOMER_AREAS:
            raise ValueError(f"Unexpected client area in row {source_row}: {area!r}")
        if not isinstance(raw[2], str) or not raw[2].strip():
            raise ValueError(f"Missing customer example title in row {source_row}")
        examples.append({
            "id": f"customer-signal-{number:03d}", "client_area_id": "customer-area-" +
            {"Индустриальный ИИ": "industrial-ai", "Инфраструктура ИИ": "ai-infrastructure",
             "Роботы": "robots", "Финтех": "fintech", "Защита ИИ": "ai-security", "Edge": "edge"}[area],
            "client_area_ru": area, "title_original": raw[2], "role": "customer_supplied_signal_example",
            "companies_original": _value(raw[4]), "rationale_original": _value(raw[5]),
            "stage_original": _value(raw[6]), "mention_trend_original": _value(raw[7]),
            "client_score_original": raw[8], "sources_original": _value(raw[9]),
            "reference_urls": _links(_value(raw[9])),
            "query_draft": {"ru": _value(copy[3]),
                            "en_terms": [term.strip() for term in _value(copy[4]).split(";") if term.strip()],
                            "status": "unreviewed_source_draft"},
            "ipc_draft": _value(copy[5]), "investment_terms_draft": _value(copy[6]),
            "media_draft": _value(copy[7]), "exclusions": [],
            "exclusions_status": "not_prepared", "hypothesis_type": "unclassified",
            "classification_status": "not_reviewed", "independent_gold_label": None,
            "discovery_evaluation_warning": "seeded retrieval is not independent signal detection",
            "source": _source(customer_path, CUSTOMER_SHEET, source_row),
            "draft_source": _source(areas_path, DERIVED_SHEET, source_row - 1),
        })
    client_areas = [
        {"id": "customer-area-" + suffix, "name_ru": area,
         "role": "client_search_area_not_signal",
         "direction_hints": [direction_ids[name] for name in CUSTOMER_DIRECTION_HINTS[area]],
         "mapping_status": "draft_area_level_navigation_not_relevance_label"}
        for area, suffix in (("Индустриальный ИИ", "industrial-ai"), ("Инфраструктура ИИ", "ai-infrastructure"),
                             ("Роботы", "robots"), ("Финтех", "fintech"), ("Защита ИИ", "ai-security"),
                             ("Edge", "edge"))
    ]
    catalog = {
        "version": VERSION,
        "policy": {"national_rows_are_signals": False, "customer_examples_are_independent_gold": False,
                   "user_query_may_be_narrowed_automatically": False,
                   "unknown_query_uses_general_route": True,
                   "draft_queries_may_be_executed_without_review": False},
        "directions": directions, "national_search_areas": search_areas,
        "client_search_areas": client_areas, "customer_examples": examples,
        "source_workbooks": [
            {"file": customer_path.name, "sha256": _sha256(customer_path), "role": "authoritative_client_100"},
            {"file": areas_path.name, "sha256": _sha256(areas_path), "role": "derived_100_and_national_89"},
        ],
        "counts": {"directions": len(directions), "national_search_areas": len(search_areas),
                   "client_search_areas": len(client_areas), "customer_examples": len(examples),
                   "customer_area_distribution": dict(sorted(Counter(item["client_area_ru"] for item in examples).items()))},
    }
    return catalog


def write_snapshot(catalog: dict, output: str | Path) -> Path:
    """Write a portable snapshot once; a changed source requires a new version path."""
    path = Path(output)
    rendered = json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise FileExistsError(f"Existing catalog differs; use a new version path: {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return path
