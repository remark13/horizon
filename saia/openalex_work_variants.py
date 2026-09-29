"""Conservative duplicate-version families inside a saved OpenAlex cohort.

Different OpenAlex IDs/DOIs can be versions of one study. We collapse only an
exact normalized long title with the same non-empty author set and publication
dates within one year of the family's earliest version. All source IDs remain
in the audit. This is not a general semantic deduplicator.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
import unicodedata


VERSION = "openalex-exact-title-author-variant-family-v1"
MAX_SPAN_DAYS = 365
MIN_TITLE_TOKENS = 6


def _words(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(re.findall(r"\w+", normalized, flags=re.UNICODE))


def _authors(values: list[str] | None) -> tuple[str, ...]:
    return tuple(sorted({" ".join(_words(value)) for value in (values or [])
                         if _words(value)}))


def collapse(rows: list[dict]) -> tuple[list[dict], dict]:
    seen = set()
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    singles = []
    for row in rows:
        identifier = row["openalex_id"]
        if not isinstance(identifier, str) or identifier in seen:
            raise ValueError("Duplicate or missing OpenAlex ID before variant analysis")
        seen.add(identifier)
        date.fromisoformat(row["publication_date"])
        title = _words(row.get("title") or "")
        authors = _authors(row.get("authors"))
        if len(title) < MIN_TITLE_TOKENS or not authors:
            singles.append(row)
        else:
            buckets[(title, authors)].append(row)
    representatives = list(singles)
    families = []
    for key, cohort in sorted(buckets.items()):
        ordered = sorted(cohort, key=lambda item: (
            item["publication_date"], item["openalex_id"]))
        while ordered:
            anchor = ordered[0]
            anchor_day = date.fromisoformat(anchor["publication_date"])
            family = [row for row in ordered if (
                date.fromisoformat(row["publication_date"]) - anchor_day).days <= MAX_SPAN_DAYS]
            ordered = ordered[len(family):]
            canonical = dict(anchor)
            canonical["source_openalex_variant_ids"] = sorted(
                row["openalex_id"] for row in family)
            if any("source_mission_ids" in row for row in family):
                canonical["source_mission_ids"] = sorted({mission for row in family
                                                          for mission in (
                                                              row.get("source_mission_ids") or [])})
            representatives.append(canonical)
            if len(family) > 1:
                families.append({
                    "representative_openalex_id": canonical["openalex_id"],
                    "source_openalex_variant_ids": canonical["source_openalex_variant_ids"],
                    "title": canonical["title"],
                    "authors": list(key[1]),
                    "first_publication_date": anchor["publication_date"],
                    "last_publication_date": family[-1]["publication_date"],
                })
    representatives.sort(key=lambda item: (
        item["publication_date"], item["openalex_id"]))
    if len(representatives) + sum(len(row["source_openalex_variant_ids"]) - 1
                                  for row in families) != len(rows):
        raise ValueError("Variant-family accounting does not reconcile")
    return representatives, {
        "version": VERSION,
        "input_openalex_ids": len(rows),
        "representative_works": len(representatives),
        "variant_rows_collapsed": len(rows) - len(representatives),
        "families_with_multiple_ids": len(families),
        "families": families,
        "policy": {
            "exact_normalized_title": True,
            "minimum_title_tokens": MIN_TITLE_TOKENS,
            "same_nonempty_author_set": True,
            "maximum_days_from_earliest": MAX_SPAN_DAYS,
            "missing_author_or_short_title": "never_merge",
            "different_title_or_author": "never_merge",
            "semantic_identity_verified": False,
        },
    }
