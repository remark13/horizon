#!/usr/bin/env python3
"""Verify a downloaded result file as data, without any code/network actions."""
import argparse
import json
from pathlib import Path

from saia.hybrid import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.path.stat().st_size > 20_000_000:
        raise ValueError("Result file exceeds verification limit")
    report = json.loads(args.path.read_text(encoding="utf-8"))
    body = {k: v for k, v in report.items() if k != "report_payload_sha256"}
    valid = report.get("report_payload_sha256") == digest(body)
    result = {"file": args.path.name, "payload_checksum_valid": valid, "version": report.get("version"),
              "exported_count": (report.get("scope") or {}).get("exported_count"),
              "network_requested": report.get("network_requested"), "model_generation_requested": report.get("model_generation_requested")}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if valid else 1)


if __name__ == "__main__":
    main()
