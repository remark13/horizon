from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.arxiv_parent_index_comparison import compare


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Comparison output is immutable")
    result = compare(index_dir=args.index, reports_dir=args.reports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"reports_compared": result["reports_compared"],
                      "reports_exact": result["reports_exact"],
                      "months_compared": result["months_compared"],
                      "months_mismatched": result["months_mismatched"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
