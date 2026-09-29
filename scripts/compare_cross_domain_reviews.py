"""Compare two complete reviews of the same frozen article-topic packet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.cross_domain_relevance_review import compare
from saia.cross_domain_relevance_packet import json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--left-review", type=Path, required=True)
    parser.add_argument("--right-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Review comparison output is immutable")
    report = compare(packet_path=args.packet, audit_path=args.audit,
                     left_review_path=args.left_review,
                     right_review_path=args.right_review)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json_bytes(report))
    print(json.dumps({"items": report["topical_relevance_agreement"]["items"],
                      "topical_agreement": report["topical_relevance_agreement"][
                          "exact_agreement"],
                      "disagreements": len(report["disagreement_item_ids_for_adjudication"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
