from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_cross_source_links import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arxiv", type=Path, required=True)
    parser.add_argument("--openalex", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(arxiv_dir=args.arxiv, openalex_dir=args.openalex,
                   output=args.output)
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
