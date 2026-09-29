"""Freeze every publication behind an existing scout candidate for review.

Read-only: never launches discovery, changes a score, or creates expert labels.
The packet is a diagnostic of topic composition, not quality ground truth.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from saia import db
from saia.candidates import composition_sha256, export_cards
from saia.triage import build_queue


def assemble_packet(queue: dict, works: list[tuple], identifiers: list[tuple]) -> dict:
    """Join complete membership to ranked cards, failing on partial coverage."""
    topic_ids = {item["topic_id"] for item in queue["queue"]}
    by_topic: dict[int, list[dict]] = {topic_id: [] for topic_id in topic_ids}
    by_work: dict[int, list[dict]] = {}
    seen_memberships: set[tuple[int, int]] = set()
    for topic_id, work_id, title, abstract, effective_date, kind, language in works:
        if topic_id not in topic_ids or (topic_id, work_id) in seen_memberships:
            raise ValueError("Unexpected or repeated topic membership")
        seen_memberships.add((topic_id, work_id))
        work = {
            "work_id": work_id,
            "title": title,
            "abstract": abstract,
            "effective_date": effective_date.isoformat() if effective_date else None,
            "type": kind,
            "language": language,
            "identifiers": [],
        }
        by_topic[topic_id].append(work)
        by_work.setdefault(work_id, []).append(work)
    for work_id, kind, value in identifiers:
        if work_id not in by_work:
            raise ValueError("Identifier without a selected work")
        for work in by_work[work_id]:
            work["identifiers"].append({"kind": kind, "value": value})

    cards = []
    for item in queue["queue"]:
        members = by_topic[item["topic_id"]]
        if not members:
            raise ValueError(f"No membership for topic {item['topic_id']}")
        actual_hash = composition_sha256([work["work_id"] for work in members])
        if actual_hash != item["composition_sha256"]:
            raise ValueError(f"Composition changed for topic {item['topic_id']}")
        cards.append({
            "rank": item["rank"],
            "candidate_id": item["candidate_id"],
            "topic_id": item["topic_id"],
            "label": item["card"]["label"],
            "screening": item["screening"],
            "status": item["card"]["status"],
            "composition_sha256": actual_hash,
            "member_count": len(members),
            "works": sorted(members, key=lambda work: (work["effective_date"] or "", work["work_id"])),
        })
    return {
        "version": "scout-composition-review-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mission_id": queue["mission_id"],
        "score_run_id": queue["score_run_id"],
        "cards": cards,
        "total_memberships": sum(card["member_count"] for card in cards),
        "limitations": [
            "Developer diagnostic only; no independent expert quality labels.",
            "The bounded discovery corpus is not a complete field-level publication series.",
            "Current canonical abstracts and titles are not guaranteed first versions.",
            "A card can mix different research lines despite a plausible label.",
        ],
    }


def build(mission_id: str, score_run_id: int, max_cards: int = 100,
          max_works_per_card: int = 500) -> dict:
    queue = build_queue(export_cards(mission_id, score_run_id), limit=max_cards)
    topic_ids = [item["topic_id"] for item in queue["queue"]]
    if not topic_ids:
        raise ValueError("No cards in selected score run")
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT tm.topic_id, w.work_id, w.canonical_title,
                          w.abstract, w.effective_date, w.type, w.language
                   FROM topic_membership tm JOIN work w ON w.work_id=tm.work_id
                   WHERE tm.topic_id=ANY(%s) AND w.mission_id=%s
                   ORDER BY tm.topic_id, w.work_id""",
                (topic_ids, mission_id),
            )
            works = cur.fetchall()
            counts: dict[int, int] = {}
            for topic_id, *_ in works:
                counts[topic_id] = counts.get(topic_id, 0) + 1
            if any(count > max_works_per_card for count in counts.values()):
                raise ValueError("A card exceeds the explicit full-review cap")
            work_ids = sorted({row[1] for row in works})
            cur.execute(
                """SELECT work_id, kind, value FROM identifier
                   WHERE mission_id=%s AND work_id=ANY(%s)
                   ORDER BY work_id, kind, value""",
                (mission_id, work_ids),
            )
            identifiers = cur.fetchall()
    return assemble_packet(queue, works, identifiers)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mission_id")
    parser.add_argument("score_run_id", type=int)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new immutable path")
    packet = build(args.mission_id, args.score_run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(packet, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print(json.dumps({"cards": len(packet["cards"]),
                      "total_memberships": packet["total_memberships"],
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
