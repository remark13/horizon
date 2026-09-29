from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_relevance_packet import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-case", type=int, default=6)
    parser.add_argument("--offset-per-case", type=int, default=0)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Review packet already exists; choose a new version")
    packet = build(corpus_dir=args.corpus, catalog_path=args.catalog, cases_path=args.cases,
                   per_case=args.per_case, offset_per_case=args.offset_per_case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "items": packet["selection"]["items"],
                      "empty_cases": packet["selection"]["empty_cases"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
