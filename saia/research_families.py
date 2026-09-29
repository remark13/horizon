"""Read-only, conservative suggestions for possible research-version families.

A suggestion is not a canonical merge. Distinct DOI records and their source
provenance must remain intact until a reviewed research-family policy exists.
"""

from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
from itertools import combinations
from collections import defaultdict
import re
import unicodedata


POLICY = "research-version-family-suggestions-v1"


def _text(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def _authors(work: dict) -> set[str]:
    names = set()
    for value in work.get("authors") or []:
        parts = _text(value).split()
        if len(parts) >= 2:
            names.add(parts[-1] + ":" + parts[0][0])
    return names


def version_pair(left: dict, right: dict) -> dict | None:
    """Find near-identical records; never assert they are the same study."""
    title = _text(left.get("title"))
    if len(title) < 25 or title != _text(right.get("title")):
        return None
    first, second = left.get("published_at"), right.get("published_at")
    try:
        date_gap = abs((date.fromisoformat(first[:10]) -
                        date.fromisoformat(second[:10])).days)
    except (TypeError, ValueError):
        return None
    if date_gap > 366:
        return None
    authors_left, authors_right = _authors(left), _authors(right)
    shared = authors_left & authors_right
    if len(shared) < 2 or len(shared) * 2 < min(len(authors_left), len(authors_right)):
        return None
    abstract_left, abstract_right = _text(left.get("abstract")), _text(right.get("abstract"))
    if min(len(abstract_left), len(abstract_right)) < 120:
        return None
    similarity = SequenceMatcher(None, abstract_left, abstract_right).ratio()
    if similarity < 0.84:
        return None
    return {"title": left["title"], "left_source_ids": left.get("source_ids") or [],
            "right_source_ids": right.get("source_ids") or [],
            "left_doi": left.get("doi"), "right_doi": right.get("doi"),
            "shared_author_keys": sorted(shared), "date_gap_days": date_gap,
            "abstract_similarity": round(similarity, 4),
            "decision": "possible_version_family_needs_review"}


def audit(works: list[dict]) -> dict:
    by_title: dict[str, list[dict]] = defaultdict(list)
    for work in works:
        by_title[_text(work.get("title"))].append(work)
    suggestions = [found for group in by_title.values() if len(group) > 1
                   for left, right in combinations(group, 2)
                   if (found := version_pair(left, right)) is not None]
    return {"version": POLICY, "record_count": len(works),
            "same_title_group_count": sum(len(group) > 1 for group in by_title.values()),
            "records_in_same_title_groups": sum(len(group) for group in by_title.values()
                                                if len(group) > 1),
            "candidate_pair_count": len(suggestions), "candidate_pairs": suggestions,
            "policy": {"no_records_merged": True, "no_family_count_claimed": True,
                       "not_used_in_signal_scoring": True,
                       "different_dois_preserved": True}}
