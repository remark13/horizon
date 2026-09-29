from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.concept_group_coverage import probe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    result = probe(proposals_path=args.proposals, index_dir=args.index,
                   output_path=args.output, max_cases=args.max_cases)
    print(json.dumps(result["status_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
