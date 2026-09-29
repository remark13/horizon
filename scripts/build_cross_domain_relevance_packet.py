"""Build separate immutable blind review and provenance-audit artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.cross_domain_relevance_packet import build, json_bytes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--arxiv-report", type=Path, required=True)
    parser.add_argument("--openalex-report", type=Path, required=True)
    parser.add_argument("--followup", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--packet-output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()
    if (args.packet_output == args.audit_output or args.packet_output.exists()
            or args.audit_output.exists()):
        raise FileExistsError("Choose two distinct, new output paths")
    packet, audit = build(
        plan_path=args.plan, catalog_path=args.catalog,
        arxiv_report_path=args.arxiv_report,
        openalex_report_path=args.openalex_report,
        followup_path=args.followup, index_dir=args.index)
    args.packet_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.packet_output.write_bytes(json_bytes(packet))
    args.audit_output.write_bytes(json_bytes(audit))
    print(json.dumps({"items": len(packet["items"]),
                      "packet": str(args.packet_output),
                      "audit": str(args.audit_output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
