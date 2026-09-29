"""Temporal cross-topic phrase candidates, never an automatic scientific verdict.

Uses unique works in disjoint complete windows. Active and cumulative coverage
are separate. Saved topic IDs are only proxies for subject areas; optional
lineage families conservatively prevent splits from looking like diffusion.
No target technology names or control publication IDs are used by this module.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from functools import lru_cache

from saia.source_text_view import source_text_view


VERSION = "cross-cluster-phrase-diffusion-diagnostic-v2"
TOKEN = re.compile(r"[a-zа-яё][a-zа-яё0-9]*", re.IGNORECASE)
# Grammatical words, NOT technological content such as graph/network/neural.
STOP = frozenset("the and for with from this that are using based of to in is by on "
                 "we a an as be our it can or which these their has have was were "
                 "been being than into its at also such between through without "
                 "и в во на по для из при или это как что который которые".split())
GENERIC = frozenset("new novel proposed propose paper work study studies approach "
                    "approaches method methods model models framework frameworks "
                    "system systems result results experiment experiments problem "
                    "problems performance effective efficient demonstrate present".split())


@dataclass(frozen=True)
class DiffusionPolicy:
    recent_windows: int = 3
    min_recent_works: int = 3
    min_docs_per_area: int = 1
    min_recent_areas: int = 2
    min_area_gain: int = 1
    min_share_ratio: float = 1.5
    max_recent_share: float = 0.02
    min_phrase_tokens: int = 2
    max_phrase_tokens: int = 4
    max_unique_phrases: int = 1_000_000
    top_limit: int = 200

    def validate(self):
        integer_fields = ("recent_windows", "min_recent_works", "min_docs_per_area",
                          "min_recent_areas", "min_area_gain", "min_phrase_tokens",
                          "max_phrase_tokens", "max_unique_phrases", "top_limit")
        if any(type(getattr(self, name)) is not int or getattr(self, name) < 1
               for name in integer_fields):
            raise ValueError("Positive integer policy values required")
        if not 2 <= self.min_phrase_tokens <= self.max_phrase_tokens <= 5:
            raise ValueError("Phrase sizes must be 2..5")
        if (not math.isfinite(self.min_share_ratio) or self.min_share_ratio < 1
                or not math.isfinite(self.max_recent_share)
                or not 0 < self.max_recent_share < 1):
            raise ValueError("Invalid share constraints")


def text_phrases(text: str, policy: DiffusionPolicy, *, text_mode: str = "literal") -> set[str]:
    """Contiguous source phrases; never bridge stopwords or punctuation.

    Hyphens and whitespace may connect tokens. Plurals, derivational forms and
    abbreviations remain separate: this lexical generator does not invent aliases.
    """
    view = source_text_view(text, text_mode)
    tokens = list(TOKEN.finditer(view))
    phrases = set()
    for size in range(policy.min_phrase_tokens, policy.max_phrase_tokens + 1):
        for offset in range(len(tokens) - size + 1):
            part = tokens[offset:offset + size]
            words = [token.group().casefold() for token in part]
            if (any(word in STOP or len(word) < 3 for word in words)
                    or all(word in GENERIC for word in words)):
                continue
            if any(re.fullmatch(r"[\s\-‐‑–]+", view[a.end():b.start()]) is None
                   for a, b in zip(part, part[1:])):
                continue
            phrases.add(" ".join(words))
    return phrases


def lineage_families(area_ids: set[str], edges: list[tuple[str, str]]) -> dict[str, str]:
    """Collapse related saved topics; no claim these are independent domains."""
    parent = {key: key for key in sorted(area_ids)}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for left, right in sorted(edges):
        if left not in parent or right not in parent:
            raise ValueError("Lineage edge outside frozen topic set")
        a, b = root(left), root(right)
        parent[max(a, b)] = min(a, b)
    return {key: root(key) for key in sorted(parent)}


def find_phrase_candidates(documents: list[dict], windows: list[str], *,
                           policy: DiffusionPolicy | None = None,
                           area_families: dict[str, str] | None = None,
                           coverage_comparable: bool = False,
                           phrase_mode: str = "exact", source_text_mode: str = "literal") -> dict:
    """Rank observed lexical diffusion; statistical evidence is not primacy.

    Each document: id, window, title, abstract, areas (stable IDs or empty).
    Repeated identical IDs are removed; contradictory versions are rejected.
    No candidate without two nonempty comparison periods or positive share AND
    active-area gain. Unassigned works stay in the numerator/denominator and
    evidence; they do not fabricate an extra independent area.
    """
    policy = policy or DiffusionPolicy()
    policy.validate()
    if phrase_mode not in {"exact", "source-local-regular-plural-v1"}:
        raise ValueError("Unsupported phrase normalization mode")
    source_text_view("", source_text_mode)
    if (any(not isinstance(window, str) or not window for window in windows)
            or len(windows) < 2 * policy.recent_windows or windows != sorted(set(windows))):
        raise ValueError("Need ordered unique complete comparison windows")
    recent = set(windows[-policy.recent_windows:])
    prior = set(windows[-2 * policy.recent_windows:-policy.recent_windows])
    unique = {}
    duplicates = 0
    all_areas = set()
    for raw in documents:
        if (not isinstance(raw.get("id"), str) or not raw["id"]
                or raw.get("window") not in windows
                or not isinstance(raw.get("title"), str)
                or not isinstance(raw.get("abstract", ""), str)
                or not isinstance(raw.get("areas"), list)
                or any(not isinstance(area, str) or not area for area in raw["areas"])):
            raise ValueError("Invalid or out-of-window document")
        row = {"id": raw["id"], "window": raw["window"], "title": raw["title"],
               "abstract": raw.get("abstract", ""), "areas": sorted(set(raw["areas"]))}
        if row["id"] in unique:
            if unique[row["id"]] != row:
                raise ValueError("Conflicting duplicate document/version")
            duplicates += 1
            continue
        unique[row["id"]] = row
        all_areas.update(row["areas"])
    if area_families is not None and any(area not in area_families for area in all_areas):
        raise ValueError("Missing area-family identity")
    counts = Counter(row["window"] for row in unique.values())
    prior_n = sum(counts[window] for window in prior)
    recent_n = sum(counts[window] for window in recent)
    if not prior_n or not recent_n:
        raise ValueError("Empty parent comparison period")
    members = defaultdict(set)
    title_members = defaultdict(set)
    def source_occurrences(identifier):
        from saia.scientific_aliases import document_occurrences
        row = unique[identifier]
        return document_occurrences(row["title"], row["abstract"],
                                    min_tokens=policy.min_phrase_tokens,
                                    max_tokens=policy.max_phrase_tokens,
                                    stopwords=STOP, generic=GENERIC, text_mode=source_text_mode)

    # Bounded evidence cache, not an unbounded materialization of all source spans.
    cached_occurrences = lru_cache(maxsize=64)(source_occurrences)
    for identifier, row in sorted(unique.items()):
        if phrase_mode == "exact":
            title_terms = text_phrases(row["title"], policy, text_mode=source_text_mode)
            phrases = title_terms | text_phrases(row["abstract"], policy, text_mode=source_text_mode)
        else:
            occurrences = source_occurrences(identifier)
            phrases = set(occurrences)
            title_terms = {phrase for phrase, spans in occurrences.items()
                           if any(span["field"] == "title" for span in spans)}
        for phrase in phrases:
            members[phrase].add(identifier)
        for phrase in title_terms:
            title_members[phrase].add(identifier)
        if len(members) > policy.max_unique_phrases:
            raise ValueError("Unique phrase resource limit exceeded")

    candidates = []
    rejected = Counter()
    def area_counts(ids):
        result = defaultdict(set)
        for identifier in ids:
            for area in unique[identifier]["areas"]:
                family = area_families[area] if area_families is not None else area
                result[family].add(identifier)
        return {area: len(hits) for area, hits in result.items()
                if len(hits) >= policy.min_docs_per_area}

    for phrase, ids in members.items():
        recent_ids = {identifier for identifier in ids if unique[identifier]["window"] in recent}
        prior_ids = {identifier for identifier in ids if unique[identifier]["window"] in prior}
        if len(recent_ids) < policy.min_recent_works:
            rejected["insufficient_recent_unique_works"] += 1
            continue
        recent_share, prior_share = len(recent_ids) / recent_n, len(prior_ids) / prior_n
        ratio = recent_share / prior_share if prior_share else None
        if (recent_share > policy.max_recent_share or recent_share <= prior_share
                or (ratio is not None and ratio < policy.min_share_ratio)):
            rejected["not_rare_or_no_relative_growth"] += 1
            continue
        recent_areas, prior_areas = area_counts(recent_ids), area_counts(prior_ids)
        gain = len(recent_areas) - len(prior_areas)
        if len(recent_areas) < policy.min_recent_areas or gain < policy.min_area_gain:
            rejected["no_active_area_expansion"] += 1
            continue
        if not title_members[phrase]:
            rejected["no_title_occurrence"] += 1
            continue
        series = []
        cumulative = set()
        for window in windows:
            window_ids = {identifier for identifier in ids if unique[identifier]["window"] == window}
            active = area_counts(window_ids)
            cumulative.update(active)
            series.append({"window": window, "parent_works": counts[window],
                           "phrase_works": len(window_ids),
                           "publication_share": len(window_ids) / counts[window] if counts[window] else None,
                           "active_areas": len(active), "cumulative_areas": len(cumulative)})
        evidence = []
        for identifier in sorted(ids):
            row = unique[identifier]
            evidence.append({"work_id": identifier, "window": row["window"],
                             "title": row["title"], "area_ids": row["areas"],
                             "phrase_in_title": identifier in title_members[phrase]})
            if phrase_mode != "exact":
                evidence[-1]["source_phrase_spans"] = cached_occurrences(identifier)[phrase]
        digest = hashlib.sha256(json.dumps(sorted(ids), separators=(",", ":")).encode()).hexdigest()
        candidates.append({"phrase": phrase, "cooccurring_phrases_same_works": [],
                           "first_observed_window": min(unique[identifier]["window"] for identifier in ids),
                           "prior_works": len(prior_ids), "recent_works": len(recent_ids),
                           "prior_share": prior_share, "recent_share": recent_share,
                           "share_change": recent_share - prior_share, "share_ratio": ratio,
                           "ratio_undefined_when_prior_zero": ratio is None,
                           "prior_active_areas": len(prior_areas), "recent_active_areas": len(recent_areas),
                           "active_area_gain": gain, "new_recent_areas": sorted(set(recent_areas) - set(prior_areas)),
                           "review_priority_not_probability": round((recent_share - prior_share)
                               * math.log1p(len(recent_ids)) * math.log1p(gain), 12),
                           "publication_composition_sha256": digest,
                           "series": series, "evidence": evidence,
                           "coverage_comparable": coverage_comparable,
                           "primary_result_verified": None,
                           "one_technical_line_verified": None,
                           "independent_diffusion_verified": None,
                           "weak_signal_verified": None})
    # Deduplicate evidence compositions, NOT technological concepts. Different
    # mechanisms can cooccur in the same works; all alternative phrases remain.
    grouped = defaultdict(list)
    for row in candidates:
        grouped[row["publication_composition_sha256"]].append(row)
    collapsed = []
    for rows in grouped.values():
        rows.sort(key=lambda row: (-sum(e["phrase_in_title"] for e in row["evidence"]),
                                   -len(row["phrase"].split()), row["phrase"]))
        chosen = rows[0]
        chosen["cooccurring_phrases_same_works"] = sorted(row["phrase"] for row in rows[1:])
        collapsed.append(chosen)
    collapsed.sort(key=lambda row: (-row["review_priority_not_probability"], row["phrase"]))
    output = {"version": VERSION, "policy": asdict(policy), "input_rows": len(documents),
            "unique_works": len(unique), "duplicate_rows_removed": duplicates,
            "unassigned_works": sum(not row["areas"] for row in unique.values()),
            "input_payload_sha256": hashlib.sha256(json.dumps(
                {"works": [unique[key] for key in sorted(unique)], "windows": windows,
                 "families": area_families}, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "windows": windows, "prior_parent_works": prior_n, "recent_parent_works": recent_n,
            "prior_windows": sorted(prior), "recent_windows": sorted(recent),
            "unique_phrases": len(members), "rejected_phrases": dict(sorted(rejected.items())),
            "proposals_before_composition_collapse": len(candidates),
            "candidate_compositions": len(collapsed), "rows": collapsed[:policy.top_limit],
            "production_changed": False, "weak_signal_accuracy_measured": False,
            "limitations": [
                "Lexical occurrence is not own research, primacy, or a coherent technical mechanism.",
                "Saved topic/family IDs are not independently validated subject areas.",
                "First observed period is not the first publication in the world.",
                "Zero prior hits in this corpus does not prove novelty; ratio stays undefined.",
                "Coverage comparability and historical text versions require a separate source passport.",
                "One-area emerging signals need the parallel within-topic detector.",
                "Cooccurring phrases with identical work sets are not proven aliases or one technical line.",
                "Inflection and abbreviation variants are not semantically normalized.",
            ]}
    if phrase_mode != "exact":
        output["version"] = "cross-cluster-phrase-diffusion-diagnostic-v3"
        output["phrase_mode"] = phrase_mode
        output["limitations"][-1] = "Only regular final plurals and unambiguous source-local acronym definitions are normalized."
        for candidate in output["rows"]:
            candidate["surface_forms"] = sorted({span["surface_form"] for evidence in candidate["evidence"]
                                                 for span in evidence["source_phrase_spans"]})
    if source_text_mode != "literal":
        output["version"] = "cross-cluster-phrase-diffusion-diagnostic-v3"
        output["source_text_mode"] = source_text_mode
        output["source_text_view_affected_works"] = sum(
            any(source_text_view(row[field], source_text_mode) != row[field] for field in ("title", "abstract"))
            for row in unique.values())
        output["limitations"].append("Only whitespace-adjacent escaped controls are decoded in an offset-preserving view; raw source texts are unchanged.")
    return output
