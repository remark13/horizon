"""Verify the bounded developer metadata review against its immutable packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


def check(packet_path: Path, review_path: Path) -> dict:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if packet.get("version") != "free-ru-short-application-first15-review-v1":
        raise ValueError("Unexpected packet")
    if (review.get("version") != "free-ru-short-application-metadata-review-v1"
            or review.get("packet_sha256") != sha256_file(packet_path)):
        raise ValueError("Review does not match frozen packet")
    result = []
    seen_cases = set()
    for row in review["cases"]:
        case_id = row["case_id"]
        if case_id in seen_cases:
            raise ValueError("Duplicate review case")
        seen_cases.add(case_id)
        records = [x for x in packet["records"] if x["case_id"] == case_id]
        expected = {x["retrieval_rank"] for x in records}
        positive = set(row["full_qualifiers_observed_ranks"])
        negative = set(row["not_observed_ranks"])
        if len(records) != 15 or positive & negative or positive | negative != expected:
            raise ValueError(f"Incomplete or overlapping labels: {case_id}")
        if set(map(int, row["observed_record_roles"])) != positive:
            raise ValueError(f"Record roles incomplete: {case_id}")
        result.append({"case_id": case_id, "metadata_full_observed": len(positive),
                       "metadata_not_observed": len(negative),
                       "record_roles": row["observed_record_roles"]})
    if seen_cases != {x["case_id"] for x in packet["records"]}:
        raise ValueError("Some cases not reviewed")
    return {"review_sha256": sha256_file(review_path), "packet_sha256": sha256_file(packet_path),
            "developer_diagnostic_only": True, "cases": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.packet, args.review), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
