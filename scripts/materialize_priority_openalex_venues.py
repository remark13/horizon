"""Build an immutable OpenAlex venue-metadata layer from saved pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_openalex_venues import materialize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = materialize(corpus_dir=args.corpus, raw_root=args.raw_root,
                         output_dir=args.output)
    print(json.dumps({"output": str(args.output), "counts": result["counts"],
                      "bytes": result["file"]["bytes"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
