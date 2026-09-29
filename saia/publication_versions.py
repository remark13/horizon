"""Conservative *relationships* among publication versions, not identity merges.

Different DOI records remain separate canonical works. This detector may link
strong preprint/journal version candidates for audit and later family-aware
counting, but callers must not silently collapse their source provenance.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
import re
import unicodedata


POLICY_VERSION = "publication-version-family-candidates-v2"
_PREPRINT = re.compile(r"^(10\.20944/preprints\d{6}\.\d+)\.v\d+$", re.I)
_FIGSHARE = re.compile(r"^(10\.6084/m9\.figshare\.(?:c\.)?\d+)(?:\.v\d+)?$", re.I)
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return " ".join(_PUNCT.sub(" ", normalized).split())


def _authors(values: list[str] | tuple[str, ...] | None) -> set[str]:
    result = set()
    for value in values or []:
        words = _text(value).split()
        if len(words) >= 2:
            result.add(f"{words[-1]}:{words[0][0]}")
    return result


def _preprint_stem(doi: str | None) -> str | None:
    value = str(doi or "").casefold().removeprefix("https://doi.org/")
    matched = _PREPRINT.fullmatch(value)
    return matched.group(1) if matched else None


def _explicit_doi_alias(left: str | None, right: str | None) -> str | None:
    """Only provider-declared URL/version shapes, never adjacent DOI numbers."""
    a = str(left or "").casefold().removeprefix("https://doi.org/")
    b = str(right or "").casefold().removeprefix("https://doi.org/")
    if not a or not b or a == b:
        return None
    fig_a, fig_b = _FIGSHARE.fullmatch(a), _FIGSHARE.fullmatch(b)
    if fig_a and fig_b and fig_a.group(1) == fig_b.group(1):
        return "figshare_version_and_concept_doi"
    if a.startswith("10.3389/") and b.startswith("10.3389/"):
        if a.removesuffix("/abstract") == b.removesuffix("/abstract"):
            return "frontiers_abstract_page_doi_alias"
    return None


def _near_dates(left: dict, right: dict) -> bool:
    try:
        delta = abs((date.fromisoformat(str(left["published_at"])[:10])
                     - date.fromisoformat(str(right["published_at"])[:10])).days)
    except (KeyError, TypeError, ValueError):
        return False
    return delta <= 730


def _version_reason(left: dict, right: dict) -> str | None:
    if not _near_dates(left, right):
        return None
    left_authors, right_authors = _authors(left.get("authors")), _authors(right.get("authors"))
    shared = left_authors & right_authors
    explicit_alias = _explicit_doi_alias(left.get("doi"), right.get("doi"))
    if (explicit_alias and len(left_authors) >= 2
            and left_authors == right_authors):
        return explicit_alias + "_same_title_date_and_authors"
    left_stem, right_stem = _preprint_stem(left.get("doi")), _preprint_stem(right.get("doi"))
    if left_stem and left_stem == right_stem and len(shared) >= 2:
        return "same_versioned_preprint_doi_stem_and_shared_authors"
    if not (bool(left_stem) ^ bool(right_stem)) or len(shared) < 3:
        return None
    a, b = _text(left.get("abstract")), _text(right.get("abstract"))
    if min(len(a), len(b)) < 200:
        return None
    if SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.97:
        return "preprint_journal_same_title_authors_and_near_identical_abstract"
    return None


def find_version_families(records: list[dict]) -> dict:
    """Return auditable candidate families; never mutate or remove a record."""
    identifiers = [record.get("work_id") for record in records]
    if len(identifiers) != len(set(identifiers)) or any(value is None for value in identifiers):
        raise ValueError("Version audit requires unique work IDs")
    buckets: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        key = _text(record.get("title"))
        if key:
            buckets[key].append(record)
    parent = {value: value for value in identifiers}

    def root(value):
        while parent[value] != value:
            value = parent[value]
        return value

    links = []
    for title, group in buckets.items():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                reason = _version_reason(left, right)
                if not reason:
                    continue
                a, b = root(left["work_id"]), root(right["work_id"])
                parent[max(a, b)] = min(a, b)
                links.append({"left_work_id": left["work_id"],
                              "right_work_id": right["work_id"],
                              "reason": reason, "exact_title_key": title})
    families: dict[int, list[int]] = defaultdict(list)
    for identifier in identifiers:
        families[root(identifier)].append(identifier)
    return {"policy_version": POLICY_VERSION,
            "families": [{"work_ids": sorted(values)} for values in families.values()
                         if len(values) > 1],
            "links": links,
            "interpretation": "candidate_version_relation_not_automatic_identity_merge",
            "records_removed": 0}
