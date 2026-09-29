"""Build a versioned full-index BAS line diagnostic from a frozen plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.bas_narrow_temporal import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Diagnostic output is immutable")
    report = build(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"parent_total_unique_ids": report["parent_total_unique_ids"],
                      "line_totals": {line["line_id"]: line["total_unique_ids"]
                                      for line in report["lines"]}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
