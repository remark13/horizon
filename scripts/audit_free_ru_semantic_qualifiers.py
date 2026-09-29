"""Offline qualifier mentions on frozen semantic first pages; not a relevance gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION, _matches_phrase
from saia.controlled_collection import sha256_file


VERSION = "free-ru-semantic-qualifier-audit-v1"


def audit(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cases = config.get("cases") or []
    if (config.get("version") != "free-ru-semantic-qualifier-gate-v1"
            or len(cases) != 3
            or len({case.get("case_id") for case in cases}) != 3):
        raise ValueError("Unexpected frozen qualifier audit config")
    root = config_path.resolve().parents[1]
    results = []
    for case in cases:
        source_path = root / case["source_report"]
        source = json.loads(source_path.read_text(encoding="utf-8"))
        matching = [row for row in source.get("rows") or []
                    if row.get("case_id") == case["case_id"]
                    and row.get("mode") == "semantic"]
        if (len(matching) != 1 or matching[0].get("status") != "succeeded"
                or matching[0].get("query_ru") != case["query_ru"]
                or not isinstance(matching[0].get("results"), list)):
            raise ValueError(f"Missing frozen semantic source for {case['case_id']}")
        rows = []
        concepts = case.get("required_concepts") or {}
        if not concepts or any(not alternatives for alternatives in concepts.values()):
            raise ValueError("Every case needs nonempty required concepts")
        for work in matching[0]["results"]:
            if not work.get("within_exact_period"):
                continue
            texts = [str(work.get(field) or "") for field in ("title", "abstract")]
            observed = {name: [phrase for phrase in alternatives
                               if any(_matches_phrase(text, phrase, ORTHOGRAPHIC_MATCHING_VERSION)
                                      for text in texts)]
                        for name, alternatives in concepts.items()}
            rows.append({"rank_in_first_page": work["rank_in_first_page"],
                         "openalex_id": work.get("openalex_id"),
                         "doi": work.get("doi"), "title": work.get("title"),
                         "abstract_available": bool(work.get("abstract")),
                         "matched_terms_by_concept": observed,
                         "concepts_mentioned": sum(bool(values) for values in observed.values()),
                         "all_concepts_mentioned": all(observed.values())})
        results.append({"case_id": case["case_id"], "query_ru": case["query_ru"],
                        "source_report": case["source_report"],
                        "source_sha256": sha256_file(source_path),
                        "required_concepts": concepts,
                        "semantic_first_page_in_period": len(rows),
                        "all_concepts_mentioned_count": sum(row["all_concepts_mentioned"]
                                                            for row in rows),
                        "partial_or_no_concepts_count": sum(not row["all_concepts_mentioned"]
                                                            for row in rows),
                        "missing_abstract_count": sum(not row["abstract_available"] for row in rows),
                        "rows": rows})
    return {"version": VERSION, "config_sha256": sha256_file(config_path),
            "matching_version": ORTHOGRAPHIC_MATCHING_VERSION,
            "cases": results,
            "limitations": config["interpretation"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Qualifer audit output is immutable")
    report = audit(args.config)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({row["case_id"]: [row["all_concepts_mentioned_count"],
                                       row["semantic_first_page_in_period"]]
                      for row in report["cases"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
