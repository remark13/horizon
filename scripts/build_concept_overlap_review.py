from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.concept_overlap_review import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--per-case", type=int, default=12)
    args = parser.parse_args()
    report = build(proposals_path=args.proposals, coverage_path=args.coverage,
                   index_dir=args.index, output_path=args.output,
                   case_ids=args.case_id, per_case=args.per_case)
    print(json.dumps({"cases": len(report["case_ids"]), "rows": len(report["rows"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
