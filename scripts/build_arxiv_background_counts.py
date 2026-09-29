from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.arxiv_background_counts import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first-year", type=int, default=2000)
    parser.add_argument("--last-complete-year", type=int, default=2025)
    args = parser.parse_args()
    result = build(index_dir=args.index, output_path=args.output,
                   first_year=args.first_year,
                   last_complete_year=args.last_complete_year)
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
