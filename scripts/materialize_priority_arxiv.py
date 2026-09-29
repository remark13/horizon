from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.priority_arxiv_materialize import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--full-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(catalog_path=args.catalog, cases_path=args.cases,
                     mirror_dir=args.mirror, full_report_path=args.full_report,
                     output_dir=args.output)
    print(json.dumps({"output": str(args.output), "documents": manifest["eligible_unique_documents"],
                      "assignments": manifest["case_assignments"], "files": manifest["files"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
