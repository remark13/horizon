"""Bounded read-only OpenAlex synonym probe from a pre-existing frozen config.

This diagnoses retrieval only. It does not approve plans or measure signal
quality, and the developer-authored concept groups are not independent gold.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from itertools import product
import json
from pathlib import Path
import time

import httpx

from saia.controlled_collection import sha256_file
from scripts.probe_openalex_russian_search_modes import _one


VERSION = "frozen-openalex-synonym-branches-v1"
VERSION_WITH_ABSTRACTS = "frozen-openalex-synonym-branches-v2-with-abstracts"
SOURCE_VERSION = "ru-compound-multicase-pilot-v1"
CASE_IDS = ("boundary-pe-enzyme-broad", "outside-quantum-archaeology",
            "negative-graphene-perpetual-motion")
MAX_BRANCHES = 9


def run(config_path: Path, *, client: httpx.Client | None = None,
        sleep=time.sleep, include_abstracts: bool = False) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("version") != SOURCE_VERSION:
        raise ValueError("Unknown frozen source config")
    start = date.fromisoformat(config["date_from"])
    cutoff = date.fromisoformat(config["as_of_date"])
    cases = {case["case_id"]: case for case in config["cases"]}
    if start >= cutoff or any(case_id not in cases for case_id in CASE_IDS):
        raise ValueError("Invalid frozen period or missing case")
    owned = client is None
    client = client or httpx.Client(timeout=25)
    rows = []
    try:
        first = True
        for case_id in CASE_IDS:
            case = cases[case_id]
            groups = case["concept_groups"]
            variants = list(product(*groups))
            if not 2 <= len(groups) <= 3 or len(variants) > MAX_BRANCHES:
                raise ValueError("Frozen branch expansion exceeds safe cap")
            for terms in variants:
                if not first:
                    sleep(1.2)
                first = False
                query = " ".join(terms)
                result = _one(client, query=query, mode="lexical", start=start,
                              cutoff=cutoff, per_page=25, sleep=sleep)
                # Compact but audit-friendly: preserve returned IDs and metadata,
                # not potentially large abstracts or relevance-as-truth claims.
                if not include_abstracts:
                    for work in result.get("results", []):
                        work.pop("abstract", None)
                rows.append({"case_id": case_id, "terms": list(terms),
                             "search_query": query, **result})
    finally:
        if owned:
            client.close()
    return {
        "version": VERSION_WITH_ABSTRACTS if include_abstracts else VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_config": str(config_path), "source_config_sha256": sha256_file(config_path),
        "period": {"from": start.isoformat(), "as_of_exclusive": cutoff.isoformat()},
        "rows": rows,
        "limits": {"developer_authored_source_terms": True,
                   "not_independent_gold": True, "first_page_per_branch_only": True,
                   "cross_branch_dedup_required": True,
                   "abstracts_archived": include_abstracts,
                   "not_complete_temporal_coverage_or_signal_growth": True,
                   "not_executed_in_user_route": True},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-abstracts", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe output is immutable")
    report = run(args.config, include_abstracts=args.include_abstracts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"branches": len(report["rows"]),
                      "errors": sum(row["status"] != "succeeded"
                                    for row in report["rows"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
