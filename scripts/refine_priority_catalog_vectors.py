from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_catalog_vectors import refine_with_area_hints


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--base-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = refine_with_area_hints(catalog_path=args.catalog,
                                    base_index_dir=args.base_index,
                                    output_dir=args.output)
    print(json.dumps({"output": str(args.output), "counts": result["counts"],
                      "files": result["files"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
