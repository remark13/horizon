"""Run the frozen 27-case pilot over the pinned local arXiv snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.cross_domain_pilot import build_review_artifacts, scan_local_arxiv


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arxiv-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    args = parser.parse_args()
    report = scan_local_arxiv(args.arxiv_dir)
    packet, template = build_review_artifacts(report)
    _write(args.report, report)
    _write(args.packet, packet)
    _write(args.template, template)
    print(json.dumps({
        "cases": len(report["cases"]),
        "review_items": len(packet["items"]),
        "source_rows": report["source"]["scanned_rows"],
        "report_sha256": report["report_payload_sha256"],
        "packet_sha256": packet["packet_payload_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
