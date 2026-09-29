#!/usr/bin/env python3
"""Check an actual browser-downloaded file, not just an API success message."""
import argparse
import json
from pathlib import Path

from saia.hybrid import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    report = json.loads(args.path.read_bytes())
    assert report["version"] in {"saia-result-export-v2", "saia-result-export-v3"}
    assert report["report_payload_sha256"] == digest({k: v for k, v in report.items() if k != "report_payload_sha256"})
    for item in report["additional_card_data"]:
        value = item["multisource_assessment"]
        assert value["assessment_payload_sha256"] == digest({k: v for k, v in value.items() if k != "assessment_payload_sha256"})
        assert set(value["axes"]) == {"science", "market", "patents", "investment"}
        assert '<svg' in value["visualization_html"]
    published = report.get("public_signals", {}).get("records", [])
    assert all(row["scientific_score"] is None and row["source_badge"] == "Из публичного источника"
               and row["references"] for row in published)
    summary = {"path": str(args.path), "version": report["version"], "score_run_id": report["score_run_id"],
               "candidate_count": report["scope"]["exported_count"], "report_checksum_valid": True,
               "public_signal_count": len(published), "public_sources_and_separation_verified": True,
               "all_assessment_checksums_valid": True, "all_four_axes_present": True,
               "all_diagrams_present": True, "scientific_results_modified": report["scientific_results_modified"]}
    output = ('outputs/public-signals-0.4.57-2026-09-29' if report['version'] == 'saia-result-export-v3'
              else 'outputs/scout-assessment-0.4.56-2026-09-29')
    root = Path(__file__).resolve().parents[1] / output
    root.mkdir(parents=True, exist_ok=True)
    (root / 'browser-download-check.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
