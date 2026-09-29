"""Freeze source-grounded, unlabeled composition pairs outside BAS.

The packet deliberately omits card rank, score and status from reviewer cases.
No judgement or production rule is created by this script.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "robotic_manipulation": (
        "outputs/scout-composition-review-robotic-2026-09-27-v1.json",
        "596222eee5426ed9b85a3bd9d0298ff911a817a28f97ba1a077174170dd24351",
    ),
    "artificial_intelligence": (
        "outputs/scout-composition-review-ai-2026-09-27-v1.json",
        "53021a4e7625dbed63dc5707c38d0d216e1f41a6bdf8543792481bd0fd042013",
    ),
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _paper(work: dict) -> dict:
    identifiers = work.get("identifiers") or []
    urls = []
    for identifier in identifiers:
        kind, value = identifier["kind"], identifier["value"]
        if kind == "arxiv":
            urls.append(f"https://arxiv.org/abs/{value}")
        elif kind == "doi":
            urls.append(f"https://doi.org/{value}")
        elif kind == "openalex":
            urls.append(value if str(value).startswith("http") else f"https://openalex.org/{value}")
    return {"work_id": work["work_id"], "title": work["title"],
            "abstract": work["abstract"], "effective_date": work["effective_date"],
            "source_urls": sorted(set(urls))}


def make_packet(sources: dict[str, dict]) -> dict:
    cases = []
    source_info = []
    for domain, (relative, expected_sha) in SOURCES.items():
        source = sources[domain]
        if source.get("version") != "scout-composition-review-v1":
            raise ValueError("Unknown composition packet")
        source_info.append({"domain": domain, "path": relative,
                            "sha256": expected_sha, "mission_id": source["mission_id"],
                            "score_run_id": source["score_run_id"],
                            "cards": len(source["cards"])})
        for ordinal, card in enumerate(source["cards"]):
            works = card["works"]
            if len(works) < 2 or len({work["work_id"] for work in works}) != len(works):
                raise ValueError("Invalid work population")
            key = card["composition_sha256"]
            shuffled = sorted(works, key=lambda work: _digest(f"{domain}|{key}|{work['work_id']}"))
            chronological = sorted(works, key=lambda work: (work["effective_date"] or "", work["work_id"]))
            random_pair = (shuffled[0], shuffled[1])
            span_pair = (chronological[0], chronological[-1])
            if {work["work_id"] for work in random_pair} == {work["work_id"] for work in span_pair}:
                if len(shuffled) < 3:
                    raise ValueError("Cannot form distinct diagnostic pairs")
                random_pair = (shuffled[0], shuffled[2])
            samples = [("deterministic_random", *random_pair),
                       ("chronological_span", *span_pair)]
            partition = "development" if ordinal % 2 == 0 else "holdout"
            for role, first, second in samples:
                ids = sorted((first["work_id"], second["work_id"]))
                case_id = f"cross-composition-{_digest(f'{domain}|{key}|{role}|{ids}')[:18]}"
                cases.append({"case_id": case_id, "domain": domain,
                              "query_context_ru": "Роботизированные манипуляции" if domain == "robotic_manipulation" else "Искусственный интеллект",
                              "partition": partition, "sample_role": role,
                              "card_composition_sha256": key,
                              "paper_a": _paper(first), "paper_b": _paper(second),
                              "review": {"same_research_problem": None,
                                         "same_technical_mechanism": None,
                                         "one_narrow_technology_line": None,
                                         "evidence_quote_a": None,
                                         "evidence_quote_b": None,
                                         "reason": None}})
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate case ID")
    return {"version": "cross-domain-composition-pairs-v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sources": source_info, "cases": cases,
            "review_policy": "Compare the papers' own research problem, technical mechanism and whether both support one narrow technology line. Cite exact abstract sentences. No weak-signal status from a pair.",
            "limitations": ["Unlabeled packet: no accuracy or precision claim",
                            "Two selected pairs cannot certify an entire card",
                            "Saved bounded retrieval, not complete arXiv or OpenAlex coverage",
                            "Current abstracts may differ from first preprint versions",
                            "Development and holdout split is by card, not publication or source cohort"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    sources = {}
    for domain, (relative, expected_sha) in SOURCES.items():
        path = ROOT / relative
        if sha256_file(path) != expected_sha:
            raise ValueError(f"Frozen source changed: {relative}")
        sources[domain] = json.loads(path.read_text(encoding="utf-8"))
    packet = make_packet(sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False,
                                      sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(packet["cases"]),
                      "development": sum(case["partition"] == "development" for case in packet["cases"]),
                      "holdout": sum(case["partition"] == "holdout" for case in packet["cases"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
