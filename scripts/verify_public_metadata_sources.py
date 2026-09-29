#!/usr/bin/env python3
"""Verify one saved card and selected keyless channels without re-scoring.

Read-only by default. --collect permits one bounded refresh through the real
application. The output is a new diagnostic snapshot, never a labelled signal
dataset or an accuracy benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SOURCES = ["nsf_awards", "datacite", "openaire_projects"]
KEYLESS_CHECK_SOURCES = frozenset(SOURCES + ["osti_gov", "nist_news_rss", "aist_press_rss"])


def checksum(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--mission", default="universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915")
    parser.add_argument("--score", type=int, default=9509)
    parser.add_argument("--candidate", type=int, default=7061)
    parser.add_argument("--query", default="Autonomous flight")
    parser.add_argument("--sources", default=",".join(SOURCES))
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = args.sources.split(",")
    if not 1 <= len(sources) <= 6 or len(set(sources)) != len(sources) or not set(sources) <= KEYLESS_CHECK_SOURCES:
        raise ValueError("Choose distinct supported keyless metadata sources.")
    if args.base != "http://127.0.0.1:8082":
        raise ValueError("Use only the SAIA Codex application, not the parallel project.")
    if args.output.exists():
        raise ValueError("Use a new output directory; preserve previous diagnostic snapshots.")
    prefix = f"/signals/{args.mission}/{args.candidate}"
    selected = urlencode({"score_run_id": args.score, "query": args.query, "sources": ",".join(sources)})
    def request(path: str, body: dict | None = None, *, html: bool = False):
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = Request(args.base + path, data=payload, headers={"Content-Type": "application/json"} if payload else {})
        with urlopen(req, timeout=45) as reply:
            raw = reply.read(10_000_001)
            if len(raw) > 10_000_000:
                raise ValueError("Diagnostic response exceeds the size bound.")
            return raw if html else json.loads(raw)
    health = request("/health")
    catalogue = request("/source-catalog")
    before = request(f"/signals/{args.mission}?score_run_id={args.score}")
    card = next(c for c in before["cards"] if c["candidate_id"] == args.candidate)
    if args.collect:
        collected = request(prefix + f"/source-context?score_run_id={args.score}",
                            {"query": args.query, "sources": sources})
    else:
        collected = None
    context = request(prefix + "/source-context?" + selected)
    brief = request(prefix + "/brief?" + selected, html=True)
    after = request(f"/signals/{args.mission}?score_run_id={args.score}")
    if checksum(before) != checksum(after) or context["scientific_score_modified"] is not False:
        raise ValueError("Scientific results were modified.")
    if context["binding"]["composition_sha256"] != card["composition_sha256"] or [r["source"] for r in context["reports"]] != sources:
        raise ValueError("Context is not bound to the selected card and sources.")
    if b"<script" in brief.lower() or any(r["name"].encode() not in brief for r in context["reports"]):
        raise ValueError("Offline card is unsafe or omits selected source channels.")
    report = {
        "scope": "bounded source-connection and UI/export check, not signal validation or accuracy",
        "health": health, "source_catalogue_count": len(catalogue["sources"]),
        "mission_id": args.mission, "score_run_id": args.score, "candidate_id": args.candidate,
        "query": args.query, "selected_sources": sources,
        "scientific_cards_sha256_before": checksum(before), "scientific_cards_sha256_after": checksum(after),
        "scientific_cards_unchanged": True, "scientific_jobs_started": False,
        "collection_requested": args.collect, "fetched_sources": (collected or {}).get("fetched_sources", []),
        "cache_reused_sources": (collected or {}).get("cache_reused_sources", []),
        "reports": [{"source": r["source"], "status": r["status"], "observed_count": r["observed_count"],
                     "observation_id": r["observation_id"], "retrieved_at": r["retrieved_at"], "cache_fresh": r["cache_fresh"]}
                    for r in context["reports"]],
        "material_count": context["material_count"], "all_live_responses_complete": all(r["status"] == "complete" for r in context["reports"]),
        "brief_sha256": hashlib.sha256(brief).hexdigest(), "brief_bytes": len(brief),
        "expert_validation_performed": False, "accuracy_improvement_measured": False,
    }
    args.output.mkdir(parents=True)
    (args.output / "checkpoint.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / f"card-{args.candidate}.html").write_bytes(brief)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
