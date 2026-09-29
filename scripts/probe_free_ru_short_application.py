"""Execute only structurally valid frozen short-noun proposals in OpenAlex."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import time

import httpx

from saia.controlled_collection import sha256_file
from scripts.probe_openalex_russian_search_modes import _one
from scripts.propose_free_ru_short_application import VERSION as PROPOSAL_VERSION


VERSION = "free-ru-short-application-openalex-v2"
START = date(2021, 9, 1)
CUTOFF = date(2026, 9, 1)


def run(proposal_path: Path, *, client: httpx.Client | None = None,
        sleep=time.sleep) -> dict:
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    if proposal.get("version") != PROPOSAL_VERSION or len(proposal.get("rows") or []) != 6:
        raise ValueError("Unexpected frozen proposal")
    owned = client is None
    client = client or httpx.Client(timeout=25)
    rows = []
    try:
        for case in proposal["rows"]:
            if case["status"] != "parsed":
                rows.append({"case_id": case["case_id"], "status": "skipped_unparsed"})
                continue
            if case["structural_issues"]:
                rows.append({"case_id": case["case_id"], "status": "skipped_invalid",
                             "issues": case["structural_issues"]})
                continue
            for variant in case["proposal_unreviewed"]["variants"]:
                if rows:
                    sleep(1.2)
                query = variant["technology_noun_en"] + " " + variant["application_noun_en"]
                result = _one(client, query=query, mode="lexical", start=START,
                              cutoff=CUTOFF, per_page=25, sleep=sleep,
                              exact_date_filter=True)
                rows.append({"case_id": case["case_id"], "role": case["role"],
                             "query_ru": case["query_ru"],
                             "search_query": query, "source_spans_ru": [
                                 variant["technology_source_span_ru"],
                                 variant["application_source_span_ru"]],
                             **result})
    finally:
        if owned:
            client.close()
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "proposal_sha256": sha256_file(proposal_path),
            "model_digest": proposal["model_digest"],
            "period": {"from": START.isoformat(), "as_of_exclusive": CUTOFF.isoformat()},
            "rows": rows, "predeclared_gates": proposal["predeclared_gates"],
            "limits": {"first_page_per_branch_only": True,
                       "not_independent_weak_signal_accuracy": True,
                       "not_executed_in_user_route": True,
                       "search_nouns_are_not_evidence_of_all_query_qualifiers": True}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe output is immutable")
    report = run(args.proposals)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"branches": sum("search_query" in x for x in report["rows"]),
                      "skipped": sum(x["status"].startswith("skipped") for x in report["rows"]),
                      "errors": sum(x["status"] not in {"succeeded", "skipped_invalid",
                                                         "skipped_unparsed"} for x in report["rows"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
