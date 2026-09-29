from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_arxiv_pilot import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(catalog_path=args.catalog, config_path=args.config,
                 cache_dir=args.cache, output_path=args.output)
    print(json.dumps({"output": str(args.output), "scanned_cached_documents": report["scanned_cached_documents"],
                      "counts": {case["catalog_id"]: case["matched_in_selected_cache"]
                                 for case in report["pilot_cases"]}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
