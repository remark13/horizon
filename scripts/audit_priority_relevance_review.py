from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_relevance_review import evaluate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Review audit is immutable; choose a new output")
    result = evaluate(packet_path=args.packet, corpus_manifest_path=args.corpus_manifest,
                      review_path=args.review)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["document_topic_relevance_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
