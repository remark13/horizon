from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.query_translation_pilot import diagnose


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(index_dir=args.index, output=args.output)
    print(json.dumps({"output": str(args.output), "model": result["model"],
                      "cases": [{"role": row["case_role"],
                                 "phrases": row["proposal_unreviewed"]["core_phrases_en"],
                                 "observed": row["observations"]}
                                for row in result["queries"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
