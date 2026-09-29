"""Audit developer labels for a frozen concept-overlap review packet."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from saia.controlled_collection import sha256_file


VERSION = "ru-concept-overlap-developer-audit-v1"
LABELS = {"strict", "partial", "off_topic", "uncertain"}


def audit(*, packet_path: Path, review_path: Path, output_path: Path) -> dict:
    if output_path.exists():
        raise FileExistsError("Review audit is immutable")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if (packet.get("version") != "ru-concept-overlap-review-packet-v1"
            or review.get("version") != "ru-concept-overlap-developer-review-v1"
            or review.get("policy", {}).get("not_independent_expert_review") is not True):
        raise ValueError("Unknown or misleading review policy")
    expected = {(row["case_id"], row["arxiv_id"]) for row in packet["rows"]}
    if len(expected) != len(packet["rows"]):
        raise ValueError("Duplicate item in review packet")
    seen = set()
    by_case: dict[str, Counter] = defaultdict(Counter)
    total: Counter = Counter()
    for row in review["rows"]:
        key = (row.get("case_id"), row.get("arxiv_id"))
        if key in seen or key not in expected:
            raise ValueError("Repeated or foreign review key")
        seen.add(key)
        label = row.get("label")
        if label not in LABELS or not isinstance(row.get("reason_ru"), str) or not row[
                "reason_ru"].strip():
            raise ValueError("Missing review decision or rationale")
        by_case[key[0]][label] += 1
        total[label] += 1
    if seen != expected:
        raise ValueError("Review does not cover every frozen packet row")
    report = {"version": VERSION,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "packet_sha256": sha256_file(packet_path),
              "review_sha256": sha256_file(review_path),
              "rows": len(expected),
              "label_counts": {label: total[label] for label in sorted(LABELS)},
              "by_case": [
                  {"case_id": case_id, "rows": sum(by_case[case_id].values()),
                   "label_counts": {label: by_case[case_id][label]
                                    for label in sorted(LABELS)}}
                  for case_id in packet["case_ids"]
              ],
              "interpretation": {
                  "not_independent_expert_review": True,
                  "not_population_precision": True,
                  "strict_sample_count_is_not_weak_signal_detection": True,
                  "review_based_on_title_and_current_abstract_only": True,
              }}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    return report
