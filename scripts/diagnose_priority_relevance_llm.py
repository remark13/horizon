from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_relevance_llm import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-topic-cap", type=int)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("LLM diagnostic is immutable")
    report = run(packet_path=args.packet, review_path=args.review,
                 corpus_dir=args.corpus, per_topic_cap=args.per_topic_cap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected_items": len(report["items"]),
                      "parsed_items": report["parsed_items"],
                      "failed_items": report["failed_items"],
                      "confusion": report["confusion"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
