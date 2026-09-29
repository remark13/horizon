from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.concept_overlap_semantic import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Semantic diagnostic output is immutable")
    report = run(packet_path=args.packet, review_path=args.review,
                 proposals_path=args.proposals)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"embedded_texts": report["embedded_texts"],
                      "embedding_seconds": report["embedding_seconds"],
                      "pairwise": report["pairwise"]["counts"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
