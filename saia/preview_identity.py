"""Conservative identity checks before the database normalization stage.

These checks save limited preview slots. They do not replace the canonical
identity policy in :mod:`saia.normalize` or adjudicate disputed source claims.
"""

from __future__ import annotations

from datetime import date
import re
import unicodedata


_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)([0-9]{4}\.[0-9]{4,5})", re.I)
_WORDS = re.compile(r"[^\w\s]", re.UNICODE)


def _field(item, name: str):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return " ".join(_WORDS.sub(" ", text).split())


def _authors(item) -> set[str]:
    normalized = set()
    for author in _field(item, "authors") or ():
        tokens = _text(author).split()
        if tokens:
            normalized.add(f"{tokens[-1]}:{tokens[0][0]}")
    return normalized


def _arxiv_ids(item) -> set[str]:
    values = (*(_field(item, "source_ids") or ()), *(_field(item, "urls") or ()))
    found = {match.group(1) for value in values if (match := _ARXIV.search(str(value)))}
    doi = str(_field(item, "doi") or "")
    if doi.casefold().startswith("10.48550/arxiv."):
        found.add(doi.casefold().removeprefix("10.48550/arxiv."))
    return found


def _year(item) -> int | None:
    try:
        return date.fromisoformat(str(_field(item, "published_at"))[:10]).year
    except ValueError:
        return None


def same_publication(left, right) -> bool:
    """Merge only strong IDs or exact title plus shared author and near date."""
    left_arxiv, right_arxiv = _arxiv_ids(left), _arxiv_ids(right)
    if left_arxiv and right_arxiv and left_arxiv.isdisjoint(right_arxiv):
        return False
    if left_arxiv & right_arxiv:
        return True
    left_key, right_key = str(_field(left, "canonical_key") or ""), str(_field(right, "canonical_key") or "")
    left_doi, right_doi = _field(left, "doi"), _field(right, "doi")
    if left_doi and right_doi and str(left_doi).casefold() == str(right_doi).casefold():
        # A disputed DOI should not collapse records with incompatible titles
        # AND incompatible known authors before the normalization preflight.
        authors_left, authors_right = _authors(left), _authors(right)
        if (_text(_field(left, "title")) != _text(_field(right, "title"))
                and authors_left and authors_right and authors_left.isdisjoint(authors_right)):
            return False
        return True
    if (left_doi and right_doi and not str(left_doi).casefold().startswith("10.48550/arxiv.")
            and not str(right_doi).casefold().startswith("10.48550/arxiv.")):
        return False
    if left_key == right_key and left_key and not left_key.startswith("title:"):
        return True
    title = _text(_field(left, "title"))
    if not title or title != _text(_field(right, "title")):
        return False
    authors_left, authors_right = _authors(left), _authors(right)
    if not authors_left or not authors_right or authors_left.isdisjoint(authors_right):
        return False
    year_left, year_right = _year(left), _year(right)
    return year_left is not None and year_right is not None and abs(year_left - year_right) <= 2
