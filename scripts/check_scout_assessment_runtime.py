#!/usr/bin/env python3
"""Read-only production checks. Does not create experts or alter frozen scores."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from saia.hybrid import digest

BASE = "http://127.0.0.1:8082"
OUT = Path(__file__).resolve().parents[1] / "outputs/scout-assessment-0.4.56-2026-09-29"
CONTROL = "universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915"
EXPECTED = "f9d9aa446a22d742bbc4f8d09d15b8ef11812f7633388daed1647ef94e5b992b"
RUNS = [("robot_manipulation", "universal-monthly-v2-88247f0c-3688-4a5e-90eb-da71853d7a0d", 10410),
        ("bas", "universal-monthly-v2-23e50442-faa5-431d-b0d9-a95d82dceaa5", 10413),
        ("bas_local_corpus", "universal-monthly-full-arxiv-v1-fab72bd5-1b32-4e94-8529-3af45d69aac4", 10384)]


def get(path):
    with urlopen(BASE + path, timeout=55) as response:
        body = response.read(40_000_001)
        if len(body) > 40_000_000:
            raise ValueError("Check exceeds size guard")
        return body, dict(response.headers)


def get_json(path):
    return json.loads(get(path)[0])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    control = get_json(f"/signals/{CONTROL}?score_run_id=9509")
    sha = hashlib.sha256(json.dumps(control, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert sha == EXPECTED, "Frozen scientific control changed"
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "health": get_json("/health"),
               "frozen_scientific_sha256": sha, "frozen_control_unchanged": True, "runs": []}
    for name, mission, score in RUNS:
        queue = get_json(f"/scout-results/{mission}?score_run_id={score}")
        assert queue["queue"], "Real query produced no candidates"
        (OUT / f"{name}-results.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        first = queue["queue"][0]["card"]["candidate_id"]
        profile = get_json(f"/signals/{mission}/{first}/assessment?score_run_id={score}")
        assert profile["assessment_payload_sha256"] == digest({k: v for k, v in profile.items() if k != "assessment_payload_sha256"})
        assert all(axis in profile["axes"] for axis in ("science", "market", "patents", "investment"))
        assert '<svg' in profile["visualization_html"]
        # Read only saved data: export all eligible cards so ordering can be checked.
        ids = ",".join(str(q["card"]["candidate_id"]) for q in queue["queue"])
        body, headers = get(f"/results/{mission}/export?score_run_id={score}&candidate_ids={ids}")
        export = json.loads(body)
        assert export["report_payload_sha256"] == digest({k: v for k, v in export.items() if k != "report_payload_sha256"})
        assert [q["card"]["candidate_id"] for q in export["ranked_candidates"]] == [q["card"]["candidate_id"] for q in queue["queue"]]
        assert export["network_requested"] is False and export["scientific_results_modified"] is False
        (OUT / f"{name}-export.json").write_bytes(body)
        brief, _ = get(f"/signals/{mission}/{first}/brief?score_run_id={score}")
        assert b'<svg' in brief and b'<script' not in brief
        (OUT / f"{name}-card.html").write_bytes(brief)
        visible = get_json(f"/signals/{mission}/{first}/source-context?score_run_id={score}&sources=epo_ops&all_saved=true")
        score_sources = {r["source"] for r in profile["records"]}
        assert score_sources <= {r["source"] for r in visible["reports"]}
        summary["runs"].append({"query": name, "mission_id": mission, "score_run_id": score,
            "candidate_count": len(queue["queue"]), "counts": queue["counts"],
            "first_candidate_id": first, "top_score": profile["overall_score"],
            "source_statuses": [{"source": r["source"], "status": r["status"]} for r in visible["reports"]],
            "export_checksum_verified": True, "export_ranking_matches": True, "offline_svg_verified": True,
            "external_bonus_candidates": [{"candidate_id": q["card"]["candidate_id"], "label": q["card"]["label"],
                "base": q["assessment"]["scientific_baseline"], "external_contribution": q["assessment"]["external_contribution"]}
                for q in queue["queue"] if (q["assessment"]["external_contribution"] or 0) > 0]})
    (OUT / "runtime-checks.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
