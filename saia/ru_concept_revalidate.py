"""Apply a newer structural policy to frozen model responses without rerunning it."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from saia.ru_concept_plan_pilot import _validation_v4, VERSION_V3, VERSION_V4


def revalidate(path: Path) -> dict:
    raw = path.read_bytes()
    source = json.loads(raw)
    if source.get("version") != VERSION_V3:
        raise ValueError("Expected frozen v3 model report")
    result = deepcopy(source)
    result["version"] = VERSION_V4
    result["revalidated_from_report_sha256"] = hashlib.sha256(raw).hexdigest()
    result["model_reinvoked"] = False
    result["validation_change"] = (
        "Only a literalized English connector phrase copied from the original "
        "query may pass; an unrepresented negative scope is blocked."
    )
    for row in result["rows"]:
        if row["status"] == "parsed":
            row["structural_validation"] = _validation_v4(
                row["proposal_unreviewed"], row["query_ru"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    report = revalidate(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"cases": len(report["rows"]),
                      "clean": sum(row["status"] == "parsed" and
                                   not row["structural_validation"]["issues"]
                                   for row in report["rows"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
