"""Detect loss of explicit Latin technical acronyms in proposed search groups.

This is a narrow structural guard, not a validation of translated meaning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


ACRONYM = re.compile(r"(?<![\w])[A-Z]{2,}(?:-[A-Z0-9]+)*(?![\w])")
EXPANSIONS = {
    "LLM": ("LARGE LANGUAGE MODEL",),
    "CAR-T": ("CHIMERIC ANTIGEN RECEPTOR T",),
    "UAV": ("UNMANNED AERIAL VEHICLE",),
    "CNC": ("COMPUTER NUMERICAL CONTROL",),
    "NFC": ("NEAR FIELD COMMUNICATION",),
}


def _tokens(value: str) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", value.upper()))


def assess(query_ru: str, proposal: dict) -> dict:
    required = sorted(set(ACRONYM.findall(query_ru)))
    terms = [term for group in proposal["concept_groups"]
             for term in group["alternatives_en"]]
    normalized = [f" {_tokens(term)} " for term in terms]
    missing = []
    for acronym in required:
        options = (acronym, *EXPANSIONS.get(acronym, ()))
        if not any(f" {_tokens(option)} " in term
                   for option in options for term in normalized):
            missing.append(acronym)
    return {
        "required_latin_acronyms": required,
        "missing_from_search_groups": missing,
        "passes": not missing,
        "not_semantic_translation_validation": True,
    }


def audit_report(path: Path) -> dict:
    raw = path.read_bytes()
    report = json.loads(raw)
    if report.get("version") != "ru-concept-plan-diagnostic-v2":
        raise ValueError("Expected frozen v2 concept proposals")
    rows = []
    for item in report["rows"]:
        row = {"case_id": item["case_id"], "status": item["status"]}
        if item["status"] == "parsed":
            row.update(assess(item["query_ru"], item["proposal_unreviewed"]))
        rows.append(row)
    return {
        "version": "ru-concept-acronym-guard-v1",
        "proposal_report_sha256": hashlib.sha256(raw).hexdigest(),
        "policy": "explicit_latin_acronym_or_known_full_expansion_must_appear_in_at_least_one_search_alternative",
        "limits": [
            "Only explicit Latin acronyms in the Russian query are checked.",
            "A passing proposal may still omit Russian terms or contain false synonyms.",
            "No proposal is approved or executed by this guard.",
        ],
        "counts": {
            "cases": len(rows),
            "cases_with_required_acronyms": sum(bool(row.get("required_latin_acronyms")) for row in rows),
            "cases_with_missing_acronyms": sum(bool(row.get("missing_from_search_groups")) for row in rows),
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proposals", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    result = audit_report(args.proposals)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(result["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
