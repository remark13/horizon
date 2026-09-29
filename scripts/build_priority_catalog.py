"""Build the portable 89-area / 100-example source snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_catalog import build_catalog, write_snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer", type=Path, required=True)
    parser.add_argument("--areas", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = build_catalog(args.customer, args.areas)
    path = write_snapshot(catalog, args.output)
    print(json.dumps({"output": str(path), "counts": catalog["counts"],
                      "source_workbooks": catalog["source_workbooks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
