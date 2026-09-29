"""Build a bounded OpenAlex Boolean retrieval query from approved concepts.

Only used by the explicit experimental live mode. The returned search is a
candidate prefilter; local title/abstract matching remains authoritative for
admission to the bounded scientific corpus.
"""

from __future__ import annotations

from urllib.parse import urlencode


MAX_ENCODED_SEARCH_BYTES = 3200  # room for date filter, select list and URL


def concept_expression(groups: list[list[str]]) -> str:
    if (not isinstance(groups, list) or not 2 <= len(groups) <= 5
            or any(not isinstance(group, list) or not 1 <= len(group) <= 3
                   for group in groups)):
        raise ValueError("OpenAlex Boolean search requires 2–5 non-empty concept groups")
    phrases = []
    for group in groups:
        values = []
        for phrase in group:
            if (not isinstance(phrase, str) or not phrase.strip()
                    or any(char in phrase for char in ('"', "\\", "\x00", "\n", "\r"))):
                raise ValueError("Unsafe OpenAlex Boolean phrase")
            values.append('"' + phrase.strip() + '"')
        phrases.append("(" + " OR ".join(values) + ")")
    expression = " AND ".join(phrases)
    if len(urlencode({"search": expression}).encode("utf-8")) > MAX_ENCODED_SEARCH_BYTES:
        raise ValueError("OpenAlex Boolean query exceeds safe URL budget")
    return expression
