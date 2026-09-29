from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.arxiv_parent_index_equivalence import compare


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--selected-comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Equivalence output is immutable")
    result = compare(mirror_dir=args.mirror, index_dir=args.index,
                     reports_dir=args.reports, packages_dir=args.packages,
                     selected_comparison_path=args.selected_comparison)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checked": result["old_reports_checked"],
                      "equivalent": result["old_reports_equivalent"],
                      "seconds": sum(x["seconds"] for x in result["outcomes"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
