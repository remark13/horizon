"""Bounded, unsupervised title-phrase proposals from a broad arXiv corpus.

This proposes search topics, not weak signals or paper-level relevance labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
import re
import sqlite3

from saia.arxiv_trigram_index import GUARDED_VERSION
from saia.controlled_collection import sha256_file


VERSION = "broad-title-phrase-proposals-v5"
_WORDS = re.compile(r"[a-z]+(?:[0-9]+)?")
DISPLAY_OVERLAP_JACCARD = 0.8


def _year_window(first_date: str, start: date, end: date) -> int:
    day = date.fromisoformat(first_date)
    if not start <= day < end:
        raise ValueError("Parent work outside frozen period")
    return day.year - start.year - ((day.month, day.day) < (start.month, start.day))


def _validate_config(config: dict) -> None:
    start = date.fromisoformat(config["date_from"])
    end = date.fromisoformat(config["as_of_date_exclusive"])
    years = end.year - start.year
    if (start.month != end.month or start.day != end.day or years < 5
            or not 1 <= config["recent_complete_windows"] <= 3
            or not 1 <= config["baseline_complete_windows"] <= years - config["recent_complete_windows"]
            or not 1 <= config["max_parent_works"] <= 100_000
            or not 1 <= config["top_limit"] <= 500
            or not 2 <= config["min_distinct_title_works"] <= 100
            or not 0 < config["max_phrase_parent_share"] <= 0.2
            or sorted(set(config["phrase_ngram_lengths"])) != config["phrase_ngram_lengths"]
            or not all(2 <= n <= 4 for n in config["phrase_ngram_lengths"])):
        raise ValueError("Unbounded or inconsistent phrase pilot configuration")
    for field in ("parent_terms", "coarse_substrings", "stopwords", "generic_tokens"):
        terms = config[field]
        if not terms or any(not re.fullmatch(r"[a-z][a-z ]{0,39}", term)
                            for term in terms):
            raise ValueError(f"Invalid {field}")
    context = config.get("required_context_terms", [])
    if (not isinstance(context, list) or len(context) > 12
            or any(not isinstance(term, str)
                   or not re.fullmatch(r"[a-z][a-z ]{0,39}", term)
                   for term in context)):
        raise ValueError("Invalid required_context_terms")
    if config.get("generic_token_policy", "exclude_any") not in (
            "exclude_any", "allow_with_specific"):
        raise ValueError("Invalid generic_token_policy")
    if config.get("source_type", "arxiv") not in ("arxiv", "openalex"):
        raise ValueError("Invalid source_type")
    # The SQL LIKE pass is only a coarse prefilter.  Every approved whole-term
    # parent must have a corresponding substring so it cannot vanish before the
    # final word-boundary check.  Keep the list bounded for a full local index.
    coarse = config["coarse_substrings"]
    if (len(coarse) > 12 or any(len(term) < 3 for term in coarse)
            or any(not any(term in parent for parent in config["parent_terms"])
                   for term in coarse)
            or any(not any(term in parent for term in coarse)
                   for parent in config["parent_terms"])):
        raise ValueError("Coarse prefilter must cover every broad parent term")


def _parent_regex(terms: list[str]) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(
        re.escape(term).replace(r"\ ", r"\s+") for term in terms
    ) + r")(?![a-z0-9])", re.IGNORECASE)


def _matches_parent(text: str, parent_regex: re.Pattern[str],
                    context_regex: re.Pattern[str] | None) -> bool:
    return bool(parent_regex.search(text) and (
        context_regex is None or context_regex.search(text)))


def _inflection_key(phrase: str) -> tuple[str, ...]:
    """A narrow English inflection key; not a synonym or topic classifier."""
    words = []
    for word in phrase.split():
        if len(word) > 4 and word.endswith("ies"):
            words.append(word[:-3] + "y")
        elif len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            words.append(word[:-1])
        else:
            words.append(word)
    return tuple(words)


def _display_groups(proposals: list[dict], members: dict[str, set[str]]) -> list[dict]:
    """Collapse near-identical title cohorts for display, not scientific identity.

    Only a high Jaccard overlap against a group's representative is accepted.
    Transitive chaining is intentionally avoided: A≈B and B≈C need not imply
    A≈C.  Every phrase and its own publication count remain in the report.
    """
    groups: list[dict] = []
    inflection_groups: dict[tuple[str, ...], dict] = {}
    for rank, proposal in enumerate(proposals, start=1):
        phrase = proposal["phrase_en"]
        ids = members[phrase]
        if len(ids) != proposal["title_work_count"]:
            raise ValueError("Title members do not reconcile to phrase count")
        key = _inflection_key(phrase)
        lexical_group = inflection_groups.get(key)
        chosen = (lexical_group, "lexical_inflection", None) if lexical_group else None
        if chosen is None:
            for group in groups:
                representative = members[group["representative_phrase_en"]]
                union_size = len(ids | representative)
                overlap = len(ids & representative) / union_size if union_size else 0.0
                if overlap >= DISPLAY_OVERLAP_JACCARD:
                    chosen = (group, "title_cohort_overlap", overlap)
                    break
        if chosen is None:
            group = {
                "group_id": f"display-group-{len(groups) + 1:03d}",
                "display_rank": len(groups) + 1,
                "representative_phrase_en": phrase,
                "representative_proposal_rank": rank,
                "display_label_en": phrase,
                "members": [{"phrase_en": phrase, "proposal_rank": rank,
                             "grouping_basis": "representative",
                             "jaccard_to_representative": 1.0}],
            }
            groups.append(group)
        else:
            group, basis, overlap = chosen
            group["members"].append({
                "phrase_en": phrase, "proposal_rank": rank,
                "grouping_basis": basis,
                "jaccard_to_representative": round(overlap, 4) if overlap is not None else None,
            })
            current_label = group["display_label_en"]
            if (len(phrase.split()), len(phrase)) > (
                    len(current_label.split()), len(current_label)):
                group["display_label_en"] = phrase
        inflection_groups[key] = group
        proposal["display_group_id"] = group["group_id"]
        proposal["title_work_ids"] = sorted(ids)
    return groups


def collect_parent(index_dir: Path, config: dict) -> tuple[list[dict], dict]:
    _validate_config(config)
    if config.get("source_type", "arxiv") != "arxiv":
        raise ValueError("The trigram parent collector is arXiv-only")
    manifest_path = index_dir / "manifest.json"
    if sha256_file(manifest_path) != config["index_manifest_sha256"]:
        raise ValueError("Pinned index manifest differs from plan")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    db_path = index_dir / "index.sqlite3"
    guard_info = manifest["duplicate_guard"]
    guard_path = index_dir / guard_info["name"]
    if (manifest["version"] != GUARDED_VERSION
            or not manifest["source"]["complete_pinned_inventory_indexed"]
            or db_path.stat().st_size != manifest["file"]["bytes"]
            or guard_path.stat().st_size != guard_info["bytes"]
            or sha256_file(guard_path) != guard_info["sha256"]):
        raise ValueError("Index or duplicate guard does not match manifest")
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    excluded_ids = {item["arxiv_id"] for item in guard["variants"]}
    if (len(excluded_ids) != guard["duplicate_arxiv_ids"]
            or guard["source_inventory_sha256"] != manifest["source"]["full_inventory_sha256"]):
        raise ValueError("Duplicate guard ID count differs")
    regex = _parent_regex(config["parent_terms"])
    context_regex = (_parent_regex(config["required_context_terms"])
                     if config.get("required_context_terms") else None)
    condition = " OR ".join("(w.title LIKE ? OR w.abstract LIKE ?)"
                            for _ in config["coarse_substrings"])
    params = [f"%{term}%" for term in config["coarse_substrings"] for _ in (0, 1)]
    connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    selected, coarse, excluded = [], 0, 0
    try:
        cursor = connection.execute(
            f"SELECT w.arxiv_id,w.title,w.abstract,w.first_submission_date "
            f"FROM works AS w WHERE w.first_submission_date >= ? "
            f"AND w.first_submission_date < ? AND ({condition})",
            [config["date_from"], config["as_of_date_exclusive"], *params],
        )
        for item in cursor:
            coarse += 1
            if item["arxiv_id"] in excluded_ids:
                excluded += 1
                continue
            if not _matches_parent(
                    f"{item['title'] or ''} {item['abstract'] or ''}",
                    regex, context_regex):
                continue
            selected.append(dict(item))
            if len(selected) > config["max_parent_works"]:
                raise ValueError("Broad parent exceeds frozen safety limit")
    finally:
        connection.close()
    return selected, {
        "coarse_rows": coarse, "duplicate_guard_ids_excluded": excluded,
        "selected_unique_ids": len(selected),
        "parent_predicate": (
            "whole-term parent AND at least one required context term in current title or abstract"
            if context_regex else "whole-term matching in current title or abstract"),
        "source_snapshot_revision": manifest["source"]["revision"],
        "index_manifest_sha256": config["index_manifest_sha256"],
    }


def propose(rows: list[dict], config: dict, *, collection_audit: dict) -> dict:
    _validate_config(config)
    source_type = config.get("source_type", "arxiv")
    id_key = "arxiv_id" if source_type == "arxiv" else "openalex_id"
    date_key = "first_submission_date" if source_type == "arxiv" else "publication_date"
    if len(rows) > config["max_parent_works"] or len({row[id_key] for row in rows}) != len(rows):
        raise ValueError("Parent corpus exceeds cap or contains duplicate IDs")
    start = date.fromisoformat(config["date_from"])
    end = date.fromisoformat(config["as_of_date_exclusive"])
    years = end.year - start.year
    stop = set(config["stopwords"])
    generic = set(config["generic_tokens"])
    generic_policy = config.get("generic_token_policy", "exclude_any")
    counts: Counter[str] = Counter()
    per_window: Counter[tuple[str, int]] = Counter()
    parent_per_window: Counter[int] = Counter()
    for row in sorted(rows, key=lambda item: (item[date_key], item[id_key])):
        year = _year_window(row[date_key], start, end)
        parent_per_window[year] += 1
        tokens = _WORDS.findall((row["title"] or "").casefold().replace("-", " "))
        paper_phrases = set()
        for n in config["phrase_ngram_lengths"]:
            for offset in range(len(tokens) - n + 1):
                part = tokens[offset:offset + n]
                if (any(token in stop or len(token) < 3 for token in part)
                        or (generic_policy == "exclude_any" and any(
                            token in generic for token in part))
                        or (generic_policy == "allow_with_specific" and all(
                            token in generic for token in part))):
                    continue
                paper_phrases.add(" ".join(part))
        for phrase in paper_phrases:
            counts[phrase] += 1
            per_window[(phrase, year)] += 1
    recent = config["recent_complete_windows"]
    prior = config["baseline_complete_windows"]
    recent_parent = sum(parent_per_window[year] for year in range(years - recent, years))
    prior_parent = sum(parent_per_window[year] for year in range(years - recent - prior,
                                                               years - recent))
    if not recent_parent or not prior_parent:
        raise ValueError("Broad parent has no comparable recent/prior windows")
    proposals = []
    for phrase, total in counts.items():
        if (total < config["min_distinct_title_works"]
                or total / len(rows) > config["max_phrase_parent_share"]):
            continue
        annual = [per_window[(phrase, year)] for year in range(years)]
        recent_count = sum(annual[-recent:])
        prior_count = sum(annual[-recent - prior:-recent])
        if recent_count < 2:
            continue
        # Smoothing prevents a zero baseline from being reported as infinite growth.
        recent_rate = (recent_count + 0.5) / (recent_parent + 1)
        prior_rate = (prior_count + 0.5) / (prior_parent + 1)
        relative_lift = recent_rate / prior_rate
        score = math.log1p(recent_count) * math.log2(1 + relative_lift)
        proposals.append({
            "phrase_en": phrase,
            "title_work_count": total,
            "annual_title_counts": annual,
            "recent_count": recent_count,
            "prior_count": prior_count,
            "smoothed_parent_share_lift": round(relative_lift, 3),
            "proposal_score_not_weak_signal_probability": round(score, 3),
        })
    proposals.sort(key=lambda item: (-item["proposal_score_not_weak_signal_probability"],
                                     -item["recent_count"], item["phrase_en"]))
    selected = proposals[:config["top_limit"]]
    examples: dict[str, list[dict]] = {item["phrase_en"]: [] for item in selected}
    members: dict[str, set[str]] = {item["phrase_en"]: set() for item in selected}
    for row in sorted(rows, key=lambda item: (item[date_key], item[id_key])):
        tokens = _WORDS.findall((row["title"] or "").casefold().replace("-", " "))
        present = {" ".join(tokens[offset:offset + n])
                   for n in config["phrase_ngram_lengths"]
                   for offset in range(len(tokens) - n + 1)}
        for phrase in present & examples.keys():
            members[phrase].add(row[id_key])
            sample = examples[phrase]
            if len(sample) < 3:
                url = (f"https://arxiv.org/abs/{row[id_key]}" if source_type == "arxiv"
                       else row.get("openalex_url") or f"https://openalex.org/{row[id_key]}")
                example = {id_key: row[id_key], "url": url,
                           "title": row["title"], date_key: row[date_key]}
                if source_type == "openalex":
                    example["source_openalex_variant_ids"] = row.get(
                        "source_openalex_variant_ids", [row[id_key]])
                sample.append(example)
    for item in selected:
        item["source_examples"] = examples[item["phrase_en"]]
    display_groups = _display_groups(selected, members)
    minimum_for_disjoint_top15 = 15 * config["min_distinct_title_works"]
    source_sufficiency = {
        "status": ("too_sparse_even_for_fifteen_disjoint_minimum_sized_topics"
                   if len(rows) < minimum_for_disjoint_top15 else
                   "not_proven_by_parent_size_alone"),
        "parent_works": len(rows),
        "minimum_parent_works_if_fifteen_topics_are_disjoint": minimum_for_disjoint_top15,
        "interpretation": (
            "A sparse source parent requires another source; zero proposals is not evidence of no signals."
            if len(rows) < minimum_for_disjoint_top15 else
            "Enough parent works is necessary, not sufficient, for fifteen distinct topics."),
    }
    return {
        "version": VERSION,
        "period": {"date_from": config["date_from"],
                   "as_of_date_exclusive": config["as_of_date_exclusive"],
                   "complete_year_windows": years},
        "source_type": source_type,
        "source": ("pinned local arXiv current metadata" if source_type == "arxiv"
                   else "query-specific saved OpenAlex current metadata"),
        "parent_collection": collection_audit,
        "parent_annual_counts": [parent_per_window[year] for year in range(years)],
        "source_sufficiency_for_top15": source_sufficiency,
        **({"arxiv_only_top15_source_sufficiency": source_sufficiency}
           if source_type == "arxiv" else {}),
        "phrases_above_eligibility_rules": len(proposals),
        "proposals": selected,
        "display_group_policy": {
            "method": "exact_english_inflection_or_greedy_representative_title_id_jaccard",
            "minimum_jaccard": DISPLAY_OVERLAP_JACCARD,
            "purpose": "suppress near-duplicate labels in a list, not prove one technology",
        },
        "generic_token_policy": generic_policy,
        "display_groups": display_groups,
        "top_15_distinct_display_groups": display_groups[:15],
        "limitations": [
            "This is unsupervised topic-phrase suggestion from titles, not weak-signal detection.",
            ("The broad parent is English lexical arXiv coverage, not all research in the requested area."
             if source_type == "arxiv" else
             "The parent is one query-specific OpenAlex cohort, not the complete scientific field."),
            "Phrase growth uses current metadata and does not prove first historical mention or article relevance.",
            "Overlapping phrases may describe the same work; proposal counts are not independent studies.",
            "A display group based on title-ID overlap does not establish one scientific mechanism.",
            "Author, organization, patent and commercial diffusion are not evaluated.",
        ],
        "weak_signal_verified": False,
        "relevance_reviewed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows, collection_audit = collect_parent(args.index, config)
    report = propose(rows, config, collection_audit=collection_audit)
    report["config_sha256"] = sha256_file(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"parent_works": len(rows), "eligible_phrases": report[
        "phrases_above_eligibility_rules"], "reported": len(report["proposals"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
