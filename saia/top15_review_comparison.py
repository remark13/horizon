"""Compare two complete blinded top-15 reviews without inventing consensus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from saia.cross_domain_relevance_review import _agreement


VERSION = "top15-full-composition-review-comparison-v2"
CHOICES = {"yes", "no", "uncertain", "not_assessable"}
EXTRA_COLUMNS = {"item_id", "rationale", "source_urls", "reviewer_id"}


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _read_verified_json(path: Path, *, version: str, hash_field: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = value.get(hash_field)
    body = {key: item for key, item in value.items() if key != hash_field}
    if value.get("version") != version or not expected or _digest(body) != expected:
        raise ValueError(f"Version or payload checksum mismatch: {path}")
    return value


def _review(path: Path, *, ids: set[str], questions: list[str]) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if (reader.fieldnames is None
                or len(reader.fieldnames) != len(questions) + len(EXTRA_COLUMNS)
                or set(reader.fieldnames) != set(questions) | EXTRA_COLUMNS):
            raise ValueError(f"Unexpected review columns: {path}")
        rows = list(reader)
    if len(rows) != len(ids) or {row["item_id"] for row in rows} != ids:
        raise ValueError(f"Review must contain every item exactly once: {path}")
    reviewers = {(row.get("reviewer_id") or "").strip() for row in rows}
    if len(reviewers) != 1 or not next(iter(reviewers)):
        raise ValueError(f"One nonempty reviewer_id required per file: {path}")
    for row in rows:
        if any(row[question] not in CHOICES for question in questions):
            raise ValueError(f"Invalid or missing answer: {row['item_id']}")
        if (any(row[question] != "yes" for question in questions)
                and len((row.get("rationale") or "").strip()) < 8):
            raise ValueError(f"Explain negative or uncertain answer: {row['item_id']}")
    return {"reviewer_id": next(iter(reviewers)),
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": {row["item_id"]: row for row in rows}}


def compare(packet_path: Path, private_path: Path,
            left_path: Path, right_path: Path) -> dict:
    packet = _read_verified_json(packet_path,
        version="top15-full-composition-review-packet-v2",
        hash_field="packet_payload_sha256")
    private = _read_verified_json(private_path,
        version="top15-full-composition-review-private-key-v2",
        hash_field="private_payload_sha256")
    if private.get("packet_payload_sha256") != packet["packet_payload_sha256"]:
        raise ValueError("Private key is not for this review packet")
    items = packet.get("items") or []
    ids = [item["item_id"] for item in items]
    mapping = private.get("mapping") or []
    if (not ids or len(ids) != len(set(ids)) or len(mapping) != len(ids)
            or {row["item_id"] for row in mapping} != set(ids)):
        raise ValueError("Packet/private identities are missing or duplicated")
    questions = list(packet["questions"])
    left = _review(left_path, ids=set(ids), questions=questions)
    right = _review(right_path, ids=set(ids), questions=questions)
    if left["reviewer_id"] == right["reviewer_id"] or left_path.resolve() == right_path.resolve():
        raise ValueError("Two distinct reviewer files and identifiers are required")

    agreements = {}
    for question in questions:
        agreements[question] = _agreement(
            [left["rows"][identifier][question] for identifier in ids],
            [right["rows"][identifier][question] for identifier in ids], CHOICES)
    disagreements = [identifier for identifier in ids if any(
        left["rows"][identifier][question] != right["rows"][identifier][question]
        for question in questions)]
    item_by_id = {item["item_id"]: item for item in items}
    ranked = defaultdict(list)
    for row in mapping:
        item = item_by_id[row["item_id"]]
        if not isinstance(row.get("rank"), int) or row["rank"] < 1:
            raise ValueError("Invalid saved rank")
        ranked[(item["direction"], row["mission_id"], row["score_run_id"])].append(row)
    by_direction = []
    for (direction, mission_id, score_run_id), rows in sorted(ranked.items()):
        rows.sort(key=lambda row: row["rank"])
        ranks = [row["rank"] for row in rows]
        if ranks != list(range(1, len(rows) + 1)) or len(rows) > 15:
            raise ValueError("Saved ranks are not a complete first-page prefix")
        reviewer_counts = {}
        for review in (left, right):
            labeled = [review["rows"][row["item_id"]] for row in rows]
            reviewer_counts[review["reviewer_id"]] = {
                "direction_relevance_yes": sum(row["direction_relevance"] == "yes"
                                               for row in labeled),
                "direction_relevance_unknown": sum(row["direction_relevance"] in
                    {"uncertain", "not_assessable"} for row in labeled),
                "relevant_coherent_primary_yes": sum(
                    all(row[q] == "yes" for q in (
                        "direction_relevance", "research_line_coherence",
                        "primary_result_evidence")) for row in labeled),
            }
        by_direction.append({"direction": direction, "mission_id": mission_id,
                             "score_run_id": score_run_id, "evaluated_prefix": len(rows),
                             "full_top15_available": len(rows) == 15,
                             "reviewer_counts_not_consensus": reviewer_counts,
                             "disagreement_item_ids": [row["item_id"] for row in rows
                                if row["item_id"] in disagreements]})
    return {"version": VERSION,
            "packet_payload_sha256": packet["packet_payload_sha256"],
            "reviewers": [{key: review[key] for key in ("reviewer_id", "file_sha256")}
                          for review in (left, right)],
            "items": len(ids), "agreement_by_question": agreements,
            "disagreement_item_ids_for_adjudication": disagreements,
            "by_direction": by_direction,
            "precision_at_15": None,
            "limitations": [
                "No automatic consensus: disagreements need separate adjudication.",
                "Reviewer independence is self-declared, not technically proven.",
                "Reviewer-specific counts are not validated weak-signal precision.",
                "Three saved directions do not represent all ten priority directions.",
            ]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.packet, args.private_key, args.left, args.right)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"items": result["items"],
                      "disagreements": len(result["disagreement_item_ids_for_adjudication"]),
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
