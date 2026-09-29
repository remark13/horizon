from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_relevance_semantic import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Semantic diagnostic is immutable")
    report = run(packet_path=args.packet, review_path=args.review,
                 corpus_dir=args.corpus, catalog_path=args.catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"embedded_texts": report["embedded_texts"],
                      "pairwise": report["pairwise"]["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
