"""Field-local matching for approved legacy OR and optional concept groups.

With groups, a branch matches its original literal phrases OR the conjunction
of required concepts. Alternatives within one concept remain OR. Exclusions
apply to both paths. This is retrieval only, never a relevance judgement.
"""

from __future__ import annotations

from saia.arxiv_metadata import (LITERAL_MATCHING_VERSION,
                                 ORTHOGRAPHIC_MATCHING_VERSION, _matches_phrase)


def matches_spec(row: dict, spec: dict) -> bool:
    version = spec.get("matching_version", LITERAL_MATCHING_VERSION)
    if version not in {LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION}:
        raise ValueError("Unsupported phrase matching version")
    texts = [str(row.get(field) or "") for field in ("title", "abstract")]

    def present(phrase: str) -> bool:
        return any(_matches_phrase(text, phrase, version) for text in texts)

    literals = any(present(phrase) for phrase in spec["included_phrases"])
    groups = spec.get("concept_groups") or []
    compound = bool(groups) and all(any(present(phrase) for phrase in group)
                                    for group in groups)
    excluded = any(present(phrase) for phrase in spec.get("excluded_phrases") or [])
    return (literals or compound) and not excluded


def concept_coverage(row: dict, concept_groups: list[list[str]], *,
                     matching_version: str = LITERAL_MATCHING_VERSION) -> dict:
    """Report explicit metadata coverage; never infer relevance or absence.

    Unlike ``matches_spec``, a literal retrieval hit cannot bypass required
    concept groups. A missing abstract or unmatched group is reported as
    unobserved, not as proof that the full paper lacks the concept.
    """
    if matching_version not in {LITERAL_MATCHING_VERSION, ORTHOGRAPHIC_MATCHING_VERSION}:
        raise ValueError("Unsupported phrase matching version")
    if not concept_groups or any(not isinstance(group, list) or not group
                                 or any(not isinstance(phrase, str) or not phrase.strip()
                                        for phrase in group) for group in concept_groups):
        raise ValueError("Expected nonempty required concept groups")
    texts = {field: str(row.get(field) or "") for field in ("title", "abstract")}
    matched = []
    for group in concept_groups:
        hits = [{"phrase": phrase, "field": field}
                for phrase in group for field, value in texts.items()
                if value and _matches_phrase(value, phrase, matching_version)]
        matched.append(hits)
    return {"status": ("all_groups_observed" if all(matched)
                       else "incomplete_available_metadata"),
            "matched_groups": [index + 1 for index, hits in enumerate(matched) if hits],
            "unobserved_groups": [index + 1 for index, hits in enumerate(matched) if not hits],
            "evidence": matched,
            "abstract_available": bool(texts["abstract"]),
            "not_a_relevance_or_primary_result_judgement": True}
