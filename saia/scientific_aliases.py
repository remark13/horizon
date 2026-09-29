"""Limited lexical variants with exact source spans; never global acronym aliases.

Only regular English final-word plurals and explicitly defined acronyms in the
same work are considered. No target technologies, semantic synonyms, adjective
derivations or learned dictionary enter this module.
"""

from __future__ import annotations

from collections import defaultdict
import re

from saia.source_text_view import source_text_view


VERSION = "source-local-regular-plural-v1"
TOKEN = re.compile(r"[a-zа-яё][a-zа-яё0-9]*", re.IGNORECASE)
GAP = re.compile(r"[\s\-‐‑–]+")
DEFINITION = re.compile(r"\(\s*([A-Z]{2,8})s?\s*\)")
ACRONYM = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,8})s?(?![A-Za-z0-9])")
INITIAL_STOP = frozenset("a an the of and for in on at to with".split())
UNCHANGED = frozenset("series species news means lens atlas chaos canvas bias gas yes".split())


def regular_plural_key(phrase: str) -> str:
    """Conservative orthographic heuristic, not semantic equality or stemming."""
    words = phrase.casefold().split()
    if not words:
        return ""
    last = words[-1]
    if (not re.fullmatch(r"[a-z]+", last) or len(last) < 5 or last in UNCHANGED
            or last.endswith(("ss", "is", "us", "ics", "as"))):
        return " ".join(words)
    if last.endswith("ies") and len(last) > 5:
        words[-1] = last[:-3] + "y"
    elif last.endswith(("sses", "ches", "shes")):
        words[-1] = last[:-2]
    elif last.endswith("s") and not last.endswith("xes"):
        words[-1] = last[:-1]
    return " ".join(words)


def _literal_occurrences(text, field, min_tokens, max_tokens, stopwords, generic, text_mode):
    view = source_text_view(text, text_mode)
    tokens = list(TOKEN.finditer(view))
    for size in range(min_tokens, max_tokens + 1):
        for offset in range(len(tokens) - size + 1):
            part = tokens[offset:offset + size]
            words = [token.group().casefold() for token in part]
            if (any(word in stopwords or len(word) < 3 for word in words)
                    or all(word in generic for word in words)
                    or any(GAP.fullmatch(view[a.end():b.start()]) is None
                           for a, b in zip(part, part[1:]))):
                continue
            surface = " ".join(words)
            canonical = regular_plural_key(surface)
            yield canonical, {"field": field, "start": part[0].start(), "end": part[-1].end(),
                              "quote": text[part[0].start():part[-1].end()],
                              "surface_form": surface,
                              "basis": "literal" if surface == canonical else "regular_plural"}


def _definitions(text, field, text_mode):
    view = source_text_view(text, text_mode)
    for match in DEFINITION.finditer(view):
        abbreviation = match.group(1)
        tokens = list(TOKEN.finditer(view[:match.start()]))[-10:]
        for size in range(2, len(tokens) + 1):
            part = tokens[-size:]
            words = [token.group().casefold() for token in part]
            if words[0] in INITIAL_STOP:
                continue
            if (any(GAP.fullmatch(view[a.end():b.start()]) is None
                    for a, b in zip(part, part[1:]))
                    or not view[part[-1].end():match.start()].isspace()):
                continue
            initials = "".join(word[0] for word in words if word not in INITIAL_STOP).upper()
            if initials == abbreviation:
                long_form = " ".join(words)
                yield {"acronym": abbreviation, "canonical": regular_plural_key(long_form),
                       "field": field, "start": part[0].start(), "end": match.end(),
                       "quote": text[part[0].start():match.end()]}
                break


def document_occurrences(title: str, abstract: str, *, min_tokens=2, max_tokens=4,
                         stopwords=frozenset(), generic=frozenset(), text_mode="literal") -> dict[str, list[dict]]:
    """Resolve an acronym ONLY from an unambiguous definition in this work.

    A title acronym can use its own abstract's definition, with both locations
    recorded. Definitions from other documents (including later ones) never
    enter resolution. Conflicting long forms for one acronym remain unresolved.
    """
    if not isinstance(title, str) or not isinstance(abstract, str):
        raise ValueError("Source title and abstract must be text")
    if type(min_tokens) is not int or type(max_tokens) is not int or not 2 <= min_tokens <= max_tokens <= 5:
        raise ValueError("Phrase sizes must be 2..5")
    fields = (("title", title), ("abstract", abstract))
    occurrences = defaultdict(list)
    definitions = defaultdict(list)
    for field, text in fields:
        for canonical, evidence in _literal_occurrences(text, field, min_tokens, max_tokens, stopwords, generic, text_mode):
            occurrences[canonical].append(evidence)
        for definition in _definitions(text, field, text_mode):
            definitions[definition["acronym"]].append(definition)
    usable = {}
    for abbreviation, records in definitions.items():
        canonical_names = {record["canonical"] for record in records}
        if len(canonical_names) == 1:
            canonical = next(iter(canonical_names))
            if canonical in occurrences:
                usable[abbreviation] = records[0]
    for field, text in fields:
        for match in ACRONYM.finditer(text):
            definition = usable.get(match.group(1))
            if definition is None:
                continue
            occurrences[definition["canonical"]].append({
                "field": field, "start": match.start(), "end": match.end(),
                "quote": match.group(), "surface_form": match.group(),
                "basis": "source_defined_acronym", "definition": definition})
    return dict(sorted(occurrences.items()))
