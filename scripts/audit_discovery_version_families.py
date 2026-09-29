"""Audit one saved local discovery job without altering its publications."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import urlopen

from saia.research_families import audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.base != "http://127.0.0.1:8082" or args.output.exists():
        raise ValueError("Only the local SAIA API and a new output are accepted")
    with urlopen(args.base + "/jobs/" + args.job_id, timeout=30) as response:
        job = json.load(response)
    if job.get("status") != "succeeded" or not isinstance(
            (job.get("result") or {}).get("works"), list):
        raise ValueError("A completed discovery job with works is required")
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "job_id": job["job_id"], "job_result_sha256": job.get("result_sha256"),
              **audit(job["result"]["works"])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"record_count": report["record_count"],
                      "candidate_pair_count": report["candidate_pair_count"],
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
