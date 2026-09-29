"""Conservative, auditable title diagnostics for candidate evidence.

These checks are selection hints, not scientific relevance or primary-result
labels. A title can omit a relevant method; an abstract can mention a method
incidentally. Neither case may be converted into verified G6 evidence.
"""

from __future__ import annotations

import re
from collections import Counter


_SYNTHESIS_MARKERS = (
    "review", "survey", "overview", "bibliometric", "meta-analysis",
    "meta analysis", "roadmap", "perspective", "state of the art",
)
_NONRESEARCH_MARKERS = (
    "newsletter", "news bulletin", "press release", "editorial",
)
_ACCEPTANCE_CONTEXT_MARKERS = frozenset({
    "acceptance", "attitudes", "beliefs", "preferences", "perceptions",
    "responses", "valuation", "intentions",
})
_ACCEPTANCE_CONTEXT_PHRASES = (
    "attitudes toward", "willingness to pay", "purchase intention",
    "social influence", "consumer responses", "consumer acceptance",
)
_BROAD_SINGLE_TERMS = frozenset({
    "ai", "agent", "agents", "learning", "model", "models", "data",
    "system", "systems", "technology", "technologies", "computing",
    "method", "methods", "framework", "frameworks", "application",
    "applications", "analysis", "study", "studies", "research",
})


def _normalise(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _has_phrase(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {text} "


def _checkable_anchor(label: str) -> str | None:
    """Only a single explicit anchor, never a generated list of generic terms."""
    head = label.split("—", 1)[0].strip()
    if "," in head or " and " in head.casefold() or " и " in head.casefold():
        return None
    anchor = _normalise(head)
    terms = anchor.split()
    if not terms or (len(terms) == 1 and terms[0] in _BROAD_SINGLE_TERMS):
        return None
    return anchor


def _assess_title(title: str, label: str, *, context_diagnostic: bool) -> dict:
    """Build a frozen-version title hint, never a verified content label."""
    normalised = _normalise(title)
    if any(_has_phrase(normalised, _normalise(marker)) for marker in _NONRESEARCH_MARKERS):
        format_hint = "nonresearch_format"
    elif any(_has_phrase(normalised, _normalise(marker)) for marker in _SYNTHESIS_MARKERS):
        format_hint = "review_or_synthesis"
    else:
        format_hint = "unclassified"
    anchor = _checkable_anchor(label)
    if anchor is None:
        match = "label_too_broad_for_title_check"
    elif _has_phrase(normalised, anchor):
        match = "anchor_present_in_title"
    else:
        match = "anchor_not_shown_in_title"
    result = {
        "version": "title-evidence-diagnostic-v2" if context_diagnostic else "title-evidence-diagnostic-v1",
        "format_hint": format_hint,
        "title_match": match,
        "checked_anchor": anchor,
        "verified_primary_result": None,
        "verified_topical_relevance": None,
    }
    if context_diagnostic:
        terms = set(normalised.split())
        result["context_hint"] = (
            "acceptance_or_market_context"
            if ((terms & {"consumer", "consumers"}) and
                (terms & _ACCEPTANCE_CONTEXT_MARKERS))
            or any(_has_phrase(normalised, phrase)
                   for phrase in _ACCEPTANCE_CONTEXT_PHRASES)
            else "unclassified"
        )
    return result


def assess_title_v1(title: str, label: str) -> dict:
    """Preserve the original diagnostic for previously saved score runs."""
    return _assess_title(title, label, context_diagnostic=False)


def assess_title(title: str, label: str) -> dict:
    """Describe title-only evidence with a separate acceptance-context hint."""
    return _assess_title(title, label, context_diagnostic=True)


def assess_works(works: list[dict], label: str) -> tuple[dict[int, dict], dict]:
    """Assess every distinct work; return compact aggregate and by-ID hints."""
    by_id = {int(work["work_id"]): assess_title(work["title"], label) for work in works}
    if len(by_id) != len(works):
        raise ValueError("Диагностика публикаций требует уникальных work_id.")
    formats = Counter(value["format_hint"] for value in by_id.values())
    contexts = Counter(value["context_hint"] for value in by_id.values())
    matches = Counter(value["title_match"] for value in by_id.values())
    return by_id, {
        "version": "title-evidence-diagnostic-v2",
        "scope": "all_topic_works_titles_only_not_verified_relevance",
        "total_works": len(works),
        "format_hints": dict(sorted(formats.items())),
        "context_hints": dict(sorted(contexts.items())),
        "title_matches": dict(sorted(matches.items())),
        "verified_primary_results": None,
        "verified_relevant_works": None,
    }


def choose_evidence(works: list[dict], diagnostics: dict[int, dict],
                    latest_window: str, maximum: int) -> list[tuple[dict, str, str]]:
    """Prefer a title-supported non-review early example; retain uncertainty."""
    if not works or maximum < 1:
        raise ValueError("Нужны публикации и положительный лимит оснований.")
    eligible = [
        work for work in works
        if diagnostics[work["work_id"]]["title_match"] == "anchor_present_in_title"
        and diagnostics[work["work_id"]]["format_hint"] == "unclassified"
        and diagnostics[work["work_id"]].get("context_hint", "unclassified") == "unclassified"
    ]
    if eligible:
        first = min(eligible, key=lambda work: (work["effective_date"], work["work_id"]))
        first_role = "ранняя работа с совпадением в названии"
    else:
        first = min(works, key=lambda work: (work["effective_date"], work["work_id"]))
        first_role = "самая ранняя работа в выборке"

    def ordering(work: dict) -> tuple:
        diagnostic = diagnostics[work["work_id"]]
        return (
            diagnostic["format_hint"] == "unclassified",
            diagnostic.get("context_hint", "unclassified") == "unclassified",
            diagnostic["title_match"] == "anchor_present_in_title",
            work["window"] == latest_window,
            work["source_quality"],
            bool(work["abstract"]),
            work["effective_date"],
            -work["work_id"],
        )

    ranked = [first, *sorted(
        (work for work in works if work["work_id"] != first["work_id"]),
        key=ordering, reverse=True,
    )[:maximum - 1]]
    result = []
    for index, work in enumerate(ranked):
        diagnostic = diagnostics[work["work_id"]]
        role = first_role if index == 0 else (
            "пример из последнего окна" if work["window"] == latest_window
            else "публикация из выборки"
        )
        notes = []
        if diagnostic["format_hint"] == "review_or_synthesis":
            notes.append("По названию это обзор или обобщение, не проверенный первичный результат.")
        elif diagnostic["format_hint"] == "nonresearch_format":
            notes.append("По названию это не исследовательская статья.")
        if diagnostic.get("context_hint") == "acceptance_or_market_context":
            notes.append("По названию работа о восприятии или принятии технологии; это не прямое свидетельство технического метода.")
        if diagnostic["title_match"] == "anchor_present_in_title":
            notes.append("Основная формулировка темы присутствует в названии; содержание не проверено.")
        elif diagnostic["title_match"] == "anchor_not_shown_in_title":
            notes.append("Название не подтверждает связь с узкой темой; содержание требует проверки.")
        else:
            notes.append("Название темы слишком широко для проверки по названию статьи.")
        notes.append("Первичность исследования и дата возникновения технологии не установлены.")
        result.append((work, role, " ".join(notes)))
    return result
