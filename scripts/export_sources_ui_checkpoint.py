#!/usr/bin/env python3
"""Read-only export of the source/UI milestone; no scientific recomputation."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

BASE = "http://127.0.0.1:8082"
MISSION = "universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915"
SCORE = 9509
CANDIDATE = 7061


def get(path: str) -> dict:
    with urlopen(BASE + path, timeout=15) as response:
        raw = response.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError("Bounded checkpoint response exceeded")
    return json.loads(raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.is_file() else None
    prefix = f"/signals/{MISSION}/{CANDIDATE}"
    cards = get(f"/signals/{MISSION}?score_run_id={SCORE}")
    report = {"exported_at": datetime.now(timezone.utc).isoformat(), "health": get("/health"),
              "scope": "Sources and presentation only; core accuracy and ranking unchanged",
              "source_catalog": get("/source-catalog"), "scientific_cards": cards,
              "scientific_cards_sha256": hashlib.sha256(json.dumps(cards, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              "visible_queue": get(f"/triage/{MISSION}?score_run_id={SCORE}&limit=100"),
              "candidate_context": get(prefix + f"/source-context?score_run_id={SCORE}"),
              "candidate_presentation": get(prefix + f"/presentation?score_run_id={SCORE}&model=qwen3.5%3A9b"),
              "limitations": ["Not a full-project corpus export.", "Not a precision, recall or SLA evaluation.",
                  "Draft model text is not semantically verified.", "Automatic source matches are not expert validation."]}
    if previous:
        old_sources = {row["source"]: row for row in previous["candidate_context"]["reports"]}
        current_sources = {row["source"]: row for row in report["candidate_context"]["reports"]}
        report["cache_refresh_check"] = {
            "reused_good_sources": [name for name, row in old_sources.items()
                if row["status"] in {"complete", "empty_observed_response"}
                and current_sources.get(name, {}).get("observation_id") == row["observation_id"]],
            "refreshed_error_sources": [name for name, row in old_sources.items()
                if row["status"] not in {"complete", "empty_observed_response"}
                and current_sources.get(name, {}).get("observation_id") != row["observation_id"]],
            "scientific_cards_same_as_previous_checkpoint": previous["scientific_cards_sha256"] == report["scientific_cards_sha256"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "version": report["health"]["version"],
                      "sources": len(report["source_catalog"]["sources"]),
                      "visible_queue": len(report["visible_queue"]["queue"]),
                      "context_records": report["candidate_context"]["material_count"],
                      "cache_refresh_check": report.get("cache_refresh_check"),
                      "scientific_cards_sha256": report["scientific_cards_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
