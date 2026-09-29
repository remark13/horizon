from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.ru_concept_plan_pilot import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--prompt-version", choices=["v1", "v2", "v3", "v4"], default="v1")
    args = parser.parse_args()
    report = run(cases_path=args.cases, output_path=args.output,
                 model=args.model, max_cases=args.max_cases,
                 prompt_version=args.prompt_version)
    print(json.dumps({"selected": report["selected_case_count"],
                      "parsed": report["parsed_count"],
                      "failed": report["failed_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
