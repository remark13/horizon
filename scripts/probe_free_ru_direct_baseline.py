"""Exact-date OpenAlex Russian lexical baseline for the frozen three queries."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx

from saia.controlled_collection import sha256_file
from scripts.probe_free_ru_short_application import START, CUTOFF
from scripts.probe_openalex_russian_search_modes import _one


VERSION = "free-ru-direct-openalex-baseline-v1"
CASES = {"national-area-032", "national-area-039", "customer-signal-017"}


def run(config_path: Path, *, client: httpx.Client | None = None,
        sleep=time.sleep) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("version") != "free-ru-short-application-pilot-v1":
        raise ValueError("Unexpected frozen source queries")
    chosen = [x for x in config["cases"] if x["case_id"] in CASES]
    if len(chosen) != 3:
        raise ValueError("Missing frozen baseline cases")
    owned = client is None
    client = client or httpx.Client(timeout=25)
    rows = []
    try:
        for i, case in enumerate(chosen):
            if i:
                sleep(1.2)
            result = _one(client, query=case["query_ru"], mode="lexical",
                          start=START, cutoff=CUTOFF, per_page=25, sleep=sleep,
                          exact_date_filter=True)
            rows.append({"case_id": case["case_id"], "query_ru": case["query_ru"],
                         **result})
    finally:
        if owned:
            client.close()
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": sha256_file(config_path),
            "period": {"from": START.isoformat(), "as_of_exclusive": CUTOFF.isoformat()},
            "rows": rows, "max_first_page": 25,
            "not_current_product_route": True,
            "not_signal_accuracy": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Baseline output is immutable")
    report = run(args.config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({x["case_id"]: (x["status"], len(x["results"]))
                      for x in report["rows"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
