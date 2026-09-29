"""Apply the balanced merge to frozen arXiv/OpenAlex pilot samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from saia.balanced_merge import merge


def _title_key(value: str) -> str:
    normalized = re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).strip()
    return "title:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arxiv-report", type=Path, required=True)
    parser.add_argument("--openalex-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=15)
    args = parser.parse_args()
    arxiv, openalex = _read(args.arxiv_report), _read(args.openalex_report)
    arxiv_cases = {case["case_id"]: case for case in arxiv["cases"]}
    openalex_cases = {case["case_id"]: case for case in openalex["cases"]}
    profile_ids = list(dict.fromkeys(case["profile_id"] for case in arxiv["cases"]))
    profiles = []
    for profile_id in profile_ids:
        case_ids = [
            case["case_id"] for case in arxiv["cases"]
            if case["profile_id"] == profile_id
        ]
        branches = []
        for case_id in case_ids:
            local = arxiv_cases[case_id]
            remote = openalex_cases[case_id]
            branches.append({
                "branch_id": case_id,
                "sources": {
                    "arxiv": [
                        {
                            "canonical_key": _title_key(item["title"]),
                            "title": item["title"], "url": item["url"],
                            "published_at": item["first_submission_date"],
                        }
                        for item in local["review_sample"]
                    ],
                    "openalex": [
                        {
                            "canonical_key": _title_key(item["title"]),
                            "title": item["title"], "url": item.get("url"),
                            "published_at": item["published_at"],
                        }
                        for item in remote["works"]
                    ],
                },
            })
        result = merge(branches, args.top_n)
        profiles.append({
            "profile_id": profile_id,
            "branch_contributions": result["branch_contributions"],
            "source_contributions": result["source_contributions"],
            "complete_nonempty_branch_coverage": result["complete_nonempty_branch_coverage"],
            "warnings": result["warnings"],
            "results": result["results"],
        })
    report = {
        "version": "balanced-merge-pilot-diagnostic-0.4.35",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "arxiv_report_sha256": arxiv["report_payload_sha256"],
            "openalex_report_sha256": openalex["report_payload_sha256"],
        },
        "top_n_per_profile": args.top_n,
        "interpretation": (
            "Диагностика проверяет представленность ветвей и источников на frozen samples. "
            "Она не измеряет релевантность, полноту или силу слабого сигнала."
        ),
        "profiles": profiles,
    }
    raw = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["report_payload_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "profiles": len(profiles),
        "complete_branch_coverage": sum(
            item["complete_nonempty_branch_coverage"] for item in profiles
        ),
        "report_sha256": report["report_payload_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
