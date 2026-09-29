from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_holdout import freeze


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-catalog", type=Path, required=True)
    parser.add_argument("--priority-catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = freeze(args.public_catalog, args.priority_catalog, args.output)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
