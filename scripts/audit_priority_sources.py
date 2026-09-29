from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_source_inventory import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.raw_root.resolve())
    if args.output.exists():
        raise FileExistsError("Inventory output already exists; choose a new versioned path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), **report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
