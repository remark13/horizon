from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.concept_overlap_audit import audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(packet_path=args.packet, review_path=args.review,
                   output_path=args.output)
    print(json.dumps({"rows": result["rows"],
                      "label_counts": result["label_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
