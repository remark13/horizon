from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_catalog_vectors import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(catalog_path=args.catalog, output_dir=args.output)
    print(json.dumps({"output": str(args.output), "counts": result["counts"],
                      "dimension": result["embedding_dimension"],
                      "files": result["files"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
