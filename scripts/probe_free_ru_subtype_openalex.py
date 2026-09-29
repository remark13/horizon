"""Bounded OpenAlex probe of previously saved, unreviewed subtype hypotheses."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import time

import httpx

from saia.controlled_collection import sha256_file
from scripts.probe_openalex_russian_search_modes import _one
from scripts.propose_free_ru_retrieval_subtypes import VERSION as PROPOSAL_VERSION


VERSION = "free-ru-subtype-openalex-probe-v1"
START = date(2021, 9, 1)
CUTOFF = date(2026, 9, 1)
MAX_BRANCHES = 8


def run(proposal_path: Path, *, client: httpx.Client | None = None,
        sleep=time.sleep) -> dict:
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    if proposal.get("version") != PROPOSAL_VERSION or len(proposal.get("rows") or []) != 4:
        raise ValueError("Unexpected frozen proposal")
    selected = [row for row in proposal["rows"]
                if row["case_id"] in {"outside-quantum-archaeology",
                                      "outside-hydrogen-pipeline-fiber"}]
    if len(selected) != 2 or any(row["status"] != "parsed" or row["structural_issues"]
                             for row in selected):
        raise ValueError("Subtype proposals are not structurally complete")
    owned = client is None
    client = client or httpx.Client(timeout=25)
    rows = []
    try:
        for case in selected:
            parts = case["proposal_unreviewed"]["required_concepts"]
            hypotheses = case["proposal_unreviewed"]["subtype_hypotheses"]
            for hypothesis in hypotheses:
                # Keep all qualifiers, not just the subtype; this is retrieval
                # hypothesis construction, not semantic approval of the model.
                residual = [group["term_en"] for group in parts
                            if group["source_span_ru"] != hypothesis["source_span_ru"]]
                if not residual:
                    raise ValueError("A subtype cannot replace the whole query")
                query = " ".join([hypothesis["subtype_en"], *residual])
                if rows:
                    sleep(1.2)
                result = _one(client, query=query, mode="lexical", start=START,
                              cutoff=CUTOFF, per_page=25, sleep=sleep)
                for work in result.get("results", []):
                    work.pop("abstract", None)
                rows.append({"case_id": case["case_id"],
                             "source_span_ru": hypothesis["source_span_ru"],
                             "subtype_en": hypothesis["subtype_en"],
                             "required_qualifiers_en": residual,
                             "search_query": query, **result})
    finally:
        if owned:
            client.close()
    if len(rows) > MAX_BRANCHES:
        raise ValueError("Too many subtype branches")
    return {"version": VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "proposal_sha256": sha256_file(proposal_path),
            "model_digest": proposal["model_digest"],
            "period": {"from": START.isoformat(), "as_of_exclusive": CUTOFF.isoformat()},
            "rows": rows,
            "limits": {"model_subtypes_not_semantically_verified": True,
                       "model_uncertainty_prose_may_contain_unsupported_claims": True,
                       "first_page_per_branch_only": True,
                       "not_independent_retrieval_or_signal_accuracy": True,
                       "not_executed_in_user_route": True}}


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
    print(json.dumps({"branches": len(report["rows"]),
                      "errors": sum(row["status"] != "succeeded"
                                    for row in report["rows"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
