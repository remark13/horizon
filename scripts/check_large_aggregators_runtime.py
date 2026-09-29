#!/usr/bin/env python3
"""Before/after scientific integrity and a real server-side export check."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8082"
MISSION = "universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915"
SCORE = 9509
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/large-aggregators-0.4.55-2026-09-29"


def request(path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(BASE + path, data=body, headers={"Content-Type": "application/json"}), timeout=55) as response:
        raw = response.read(20_000_001)
        if len(raw) > 20_000_000:
            raise ValueError("Runtime packet exceeds check limit")
        return json.loads(raw), {key.lower(): value for key, value in response.headers.items()}


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("before", "after"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cards, _ = request(f"/signals/{MISSION}?score_run_id={SCORE}")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "health": request("/health")[0],
              "scientific_cards_sha256": sha(cards), "scientific_card_count": len(cards["cards"])}
    if args.phase == "after":
        before = json.loads((OUT / "runtime-before.json").read_text())
        report["scientific_cards_unchanged"] = before["scientific_cards_sha256"] == report["scientific_cards_sha256"]
        if not report["scientific_cards_unchanged"]:
            raise ValueError("Scientific cards changed during source stage")
        catalog, _ = request("/source-catalog")
        report["source_catalog"] = catalog
        queue, _ = request(f"/triage/{MISSION}?score_run_id={SCORE}&limit=100")
        candidate = queue["queue"][0]["card"]["candidate_id"]
        prefix = f"/signals/{MISSION}/{candidate}"
        context, _ = request(prefix + f"/source-context?score_run_id={SCORE}",
                             {"query": "robotics", "sources": ["google_news_rss", "dealroom_marketmaps", "dealroom_public_rounds"]})
        cached, _ = request(prefix + f"/source-context?score_run_id={SCORE}",
                            {"query": "robotics", "sources": ["google_news_rss", "dealroom_marketmaps", "dealroom_public_rounds"]})
        report["live_candidate_context"] = context
        report["candidate_id"] = candidate
        report["query_is_manual_diagnostic_not_established_candidate_relevance"] = True
        report["second_request_external_fetches"] = cached.get("fetched_sources")
        params = urlencode({"score_run_id": SCORE, "candidate_ids": candidate})
        snapshot, headers = request(f"/results/{MISSION}/export?{params}")
        saved_queries = [scope["query"] for scope in snapshot["additional_card_data"][0]["source_context"]["saved_query_bindings"]]
        report["server_export_includes_saved_custom_query"] = "robotics" in saved_queries
        report["server_export_scope"] = snapshot["scope"]
        report["server_export_attachment"] = headers.get("content-disposition")
        report["scientific_cards_unchanged_after_context"] = sha(request(f"/signals/{MISSION}?score_run_id={SCORE}")[0]) == report["scientific_cards_sha256"]
        (OUT / "result-snapshot-personal.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if report["second_request_external_fetches"] or not report["server_export_includes_saved_custom_query"] or not report["scientific_cards_unchanged_after_context"]:
            raise ValueError("Cache, export or scientific integrity check failed")
    (OUT / f"runtime-{args.phase}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: val for key, val in report.items() if key not in {"source_catalog", "live_candidate_context"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
