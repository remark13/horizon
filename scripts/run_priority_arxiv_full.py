from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_arxiv_full import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--cache-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(catalog_path=args.catalog, case_config_path=args.cases,
                 mirror_dir=args.mirror, cache_manifest_path=args.cache_manifest,
                 output_path=args.output)
    print(json.dumps({"output": str(args.output), "scanned_rows": report["scanned_rows"],
                      "counts": {case["catalog_id"]: case["eligible_unique_in_pinned_mirror"]
                                 for case in report["cases"]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
