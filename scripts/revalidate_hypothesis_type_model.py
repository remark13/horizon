"""Recheck saved raw local-model responses after a parser-only correction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.controlled_collection import sha256_file
from scripts.probe_hypothesis_type_model import _parse


def revalidate(source_path: Path) -> dict:
    report = json.loads(source_path.read_text(encoding="utf-8"))
    if (report.get("version") != "hypothesis-type-local-model-draft-v1"
            or report.get("scope") != "pilot16"
            or len(report.get("rows") or []) != 16):
        raise ValueError("Unexpected frozen model draft")
    for row in report["rows"]:
        raw = row.get("raw_model_response")
        if not isinstance(raw, str):
            continue
        try:
            proposal = _parse({"response": raw})
        except (ValueError, json.JSONDecodeError):
            continue
        if (row["status"] == "parsed_machine_draft"
                and row["proposal"] != proposal):
            raise ValueError("Parser-only correction changed a prior valid proposal")
        row["proposal"] = proposal
        row["status"] = "parsed_machine_draft"
        row.pop("error_type", None)
        row.pop("error_message", None)
    report["revalidated_from_report_sha256"] = sha256_file(source_path)
    report["revalidation_note"] = (
        "Saved model responses unchanged; parser rationale length cap 500 -> 1000")
    report["counts"] = {
        "selected": len(report["rows"]),
        "parsed": sum(row["status"] == "parsed_machine_draft" for row in report["rows"]),
        "needs_review": sum((row.get("proposal") or {}).get("needs_review", False)
                            for row in report["rows"]),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Revalidated model draft is immutable")
    result = revalidate(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
