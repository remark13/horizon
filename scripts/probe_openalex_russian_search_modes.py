"""Bounded lexical-versus-semantic OpenAlex probe for free Russian queries.

Semantic first-page retrieval is only a candidate source. An approximate API
year filter plus local exact-date postfilter cannot form a complete time series.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

import httpx

from saia.controlled_collection import sha256_file
from saia.discovery import OPENALEX_URL, _abstract_from_inverted


VERSION = "openalex-russian-search-modes-probe-v1"
SINGLE_CASE_VERSION = "openalex-russian-search-modes-probe-v2-single-case"
FIELDS = ("id,doi,display_name,publication_date,abstract_inverted_index,"
          "relevance_score,type")


def _one(client: httpx.Client, *, query: str, mode: str, start: date,
         cutoff: date, per_page: int, sleep, exact_date_filter: bool = False) -> dict:
    params = {"search.semantic" if mode == "semantic" else "search": query,
              "filter": (f"from_publication_date:{start.isoformat()},"
                         f"to_publication_date:{(cutoff - timedelta(days=1)).isoformat()}"
                         if exact_date_filter else f"publication_year:>{start.year - 1}"),
              "per_page": per_page, "select": FIELDS}
    key = os.environ.get("OPENALEX_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    response = None
    for attempt in range(2):
        try:
            response = client.get(OPENALEX_URL, params=params, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            if attempt == 1:
                return {"mode": mode, "status": "source_error",
                        "error_type": type(error).__name__, "results": []}
            sleep(1.2)
            continue
        if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
            sleep(1.2)
            continue
        break
    if response is None or response.status_code != 200:
        return {"mode": mode, "status": "http_error",
                "http_status": response.status_code if response is not None else None,
                "error_message": (response.text[:300] if response is not None else None),
                "results": []}
    payload = response.json()
    works = payload.get("results")
    if not isinstance(works, list) or len(works) > per_page:
        raise ValueError("Unexpected bounded OpenAlex response")
    results = []
    for index, work in enumerate(works, 1):
        published = work.get("publication_date")
        try:
            within_period = start <= date.fromisoformat(published) < cutoff
        except (TypeError, ValueError):
            within_period = False
        results.append({"rank_in_first_page": index, "openalex_id": work.get("id"),
                        "doi": work.get("doi"), "title": work.get("display_name"),
                        "abstract": _abstract_from_inverted(
                            work.get("abstract_inverted_index")),
                        "publication_date": published,
                        "within_exact_period": within_period,
                        "relevance_score": work.get("relevance_score"),
                        "type": work.get("type")})
    return {"mode": mode, "status": "succeeded", "http_status": 200,
            "returned_first_page": len(results),
            "within_exact_period_first_page": sum(row["within_exact_period"]
                                                   for row in results),
            "api_meta_count_not_used_as_temporal_denominator": (
                payload.get("meta") or {}).get("count"),
            "api_reported_cost_usd": (payload.get("meta") or {}).get("cost_usd"),
            "results": results}


def run(config_path: Path, *, client: httpx.Client | None = None,
        sleep=time.sleep) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    case_count = len(config.get("cases") or [])
    if (config.get("version") not in {VERSION, SINGLE_CASE_VERSION}
            or config.get("per_page") != 25
            or case_count != (1 if config["version"] == SINGLE_CASE_VERSION else 2)
            or len({case["case_id"] for case in config["cases"]}) != case_count):
        raise ValueError("Unexpected frozen Russian-query probe")
    start = date.fromisoformat(config["date_from"])
    cutoff = date.fromisoformat(config["as_of_date"])
    if start >= cutoff:
        raise ValueError("Invalid probe period")
    rows = []
    owned = client is None
    client = client or httpx.Client(timeout=25)
    try:
        first = True
        for case in config["cases"]:
            for mode in ("lexical", "semantic"):
                if not first:
                    sleep(1.2)
                first = False
                result = _one(client, query=case["query_ru"], mode=mode,
                              start=start, cutoff=cutoff,
                              per_page=config["per_page"], sleep=sleep)
                rows.append({"case_id": case["case_id"], "role": case["role"],
                             "query_ru": case["query_ru"], **result})
    finally:
        if owned:
            client.close()
    return {"version": config["version"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "source": "OpenAlex live API",
            "period": {"from": start.isoformat(),
                       "as_of_exclusive": cutoff.isoformat()},
            "rows": rows,
            "limits": {"first_page_only": True,
                       "api_year_filter_then_exact_local_date_postfilter": True,
                       "not_full_temporal_coverage_or_growth": True,
                       "query_texts_untranslated_russian": True,
                       "returned_works_not_weak_signals": True,
                       "no_production_route_change": True}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Probe output is immutable")
    report = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                        indent=2) + "\n", encoding="utf-8")
    print(json.dumps({row["case_id"] + "/" + row["mode"]:
                      row.get("within_exact_period_first_page", row["status"])
                      for row in report["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
