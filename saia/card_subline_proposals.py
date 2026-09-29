"""Conservative title-grounded subline suggestions inside broad cards.

This is a diagnostic proposal layer, not proof that grouped works use the
same mechanism or that a weak signal exists.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
import re


VERSION = "card-title-subline-proposals-v2"
TOKENS = re.compile(r"[a-zа-яё]+[0-9]*", re.IGNORECASE)
STOP = frozenset({
    "a", "an", "and", "as", "at", "based", "by", "for", "from", "in",
    "into", "of", "on", "or", "the", "through", "to", "toward", "towards",
    "under", "using", "via", "with", "using", "new", "study", "towards",
    "для", "и", "или", "на", "по", "при", "с", "со", "из", "в", "во",
})


def _phrases(title: str) -> set[str]:
    words = TOKENS.findall(title.casefold().replace("-", " "))
    result = set()
    for size in (2, 3, 4):
        for offset in range(len(words) - size + 1):
            part = words[offset:offset + size]
            if (part[0] in STOP or part[-1] in STOP
                    or any(len(word) < 3 or word in STOP for word in part)):
                continue
            result.add(" ".join(part))
    return result


def _url(work: dict) -> str | None:
    for identifier in work.get("identifiers") or []:
        if identifier.get("kind") == "arxiv" and identifier.get("value"):
            return f"https://arxiv.org/abs/{identifier['value']}"
        if identifier.get("kind") == "openalex" and identifier.get("value"):
            return f"https://openalex.org/{identifier['value']}"
    return None


def propose(cards: list[dict], *, min_works: int = 3,
            max_per_card: int = 3) -> dict:
    if (not 1 <= len(cards) <= 100 or not 2 <= min_works <= 20
            or not 1 <= max_per_card <= 10):
        raise ValueError("Invalid card-subline bounds")
    all_works = {}
    for card in cards:
        works = card.get("works") or []
        if (not card.get("candidate_id") or not 2 <= len(works) <= 500
                or len({work.get("work_id") for work in works}) != len(works)):
            raise ValueError("Card needs distinct work IDs and bounded composition")
        for work in works:
            identifier = work["work_id"]
            title = work.get("title")
            if not title or (identifier in all_works
                             and all_works[identifier]["title"] != title):
                raise ValueError("Conflicting or missing work title")
            all_works[identifier] = work
    global_members = defaultdict(set)
    for work in all_works.values():
        for phrase in _phrases(work["title"]):
            global_members[phrase].add(work["work_id"])
    proposals = []
    for card in cards:
        members = defaultdict(set)
        works_by_id = {work["work_id"]: work for work in card["works"]}
        for work in card["works"]:
            for phrase in _phrases(work["title"]):
                members[phrase].add(work["work_id"])
        candidates = []
        for phrase, ids in members.items():
            if len(ids) < min_works or len(ids) == len(works_by_id):
                continue
            n = len(phrase.split())
            global_count = len(global_members[phrase])
            specificity = math.log1p(len(all_works) / global_count)
            score = len(ids) * specificity * (1 + 0.12 * (n - 2))
            candidates.append((score, phrase, ids, global_count))
        candidates.sort(key=lambda row: (-row[0], -len(row[2]),
                                         -len(row[1].split()), row[1]))
        selected = []
        for score, phrase, ids, global_count in candidates:
            if any(len(ids & prior["ids"]) / len(ids | prior["ids"]) >= 0.6
                   for prior in selected):
                continue
            selected.append({"phrase": phrase, "ids": ids,
                             "global_count": global_count, "score": score})
            if len(selected) == max_per_card:
                break
        proposals.append({
            "candidate_id": card["candidate_id"], "original_label": card.get("label"),
            "original_member_count": len(card["works"]),
            "subline_proposals": [{
                "phrase": item["phrase"],
                "title_anchored_work_count_in_card": len(item["ids"]),
                "title_anchored_work_count_in_available_cards": item["global_count"],
                "source_work_ids": sorted(item["ids"]),
                "source_titles": [works_by_id[identifier]["title"]
                                  for identifier in sorted(item["ids"])],
                "source_works": [{
                    "work_id": identifier,
                    "title": works_by_id[identifier]["title"],
                    "url": _url(works_by_id[identifier]),
                } for identifier in sorted(item["ids"])],
                "proposal_score_not_signal_probability": round(item["score"], 3),
                "one_technical_line_verified": False,
            } for item in selected],
            "unassigned_work_ids": sorted(set(works_by_id) - set().union(
                *(item["ids"] for item in selected))),
        })
    return {
        "version": VERSION, "cards_processed": len(cards),
        "distinct_works_in_input": len(all_works),
        "cards_with_subline_proposals": sum(bool(row["subline_proposals"])
                                            for row in proposals),
        "proposed_sublines": sum(len(row["subline_proposals"])
                                 for row in proposals),
        "cards": proposals, "production_changed": False,
        "weak_signal_accuracy_measured": False,
        "limitations": [
            "A shared title phrase does not prove the same research task or mechanism.",
            "Only works already in saved broad cards are counted; outside recall is unknown.",
            "No article role, independent organization, historical first mention or comparable growth is checked.",
        ],
    }
