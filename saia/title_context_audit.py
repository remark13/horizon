"""Reproducible title-only context audit; never a relevance or accuracy score."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from saia.paper_evidence import assess_title


def audit_packet(path: Path) -> dict:
    raw = path.read_bytes()
    packet = json.loads(raw)
    if not isinstance(packet.get("items"), list):
        raise ValueError("Review packet must contain an items list")
    rows = []
    total = Counter()
    for item in packet["items"]:
        works = item.get("works")
        if not isinstance(works, list):
            raise ValueError("Review packet item must contain a works list")
        flagged_positions = []
        for position, work in enumerate(works, 1):
            hint = assess_title(work["title"], item["generated_label"])["context_hint"]
            if hint == "acceptance_or_market_context":
                flagged_positions.append(position)
        total["works"] += len(works)
        total["flagged"] += len(flagged_positions)
        rows.append({
            "item_id": item["item_id"],
            "direction": item["direction"],
            "generated_label": item["generated_label"],
            "works": len(works),
            "flagged_title_positions": flagged_positions,
            "majority_flagged": len(works) >= 3 and len(flagged_positions) > len(works) / 2,
        })
    return {
        "version": "title-context-audit-v1",
        "source_packet_sha256": hashlib.sha256(raw).hexdigest(),
        "source_packet_version": packet.get("version"),
        "diagnostic_version": "title-evidence-diagnostic-v2",
        "limitations": [
            "Rules were drafted after viewing a precision-fermentation card; this is a development set, not a holdout.",
            "A title hint does not prove article content, primary research, market activity, or a weak signal.",
            "Works are card memberships and can repeat across cards.",
            "No documents are excluded or reassigned by this audit.",
        ],
        "counts": {
            "cards": len(rows),
            "work_memberships": total["works"],
            "flagged_title_memberships": total["flagged"],
            "cards_with_majority_flagged": sum(row["majority_flagged"] for row in rows),
        },
        "cards": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    result = audit_packet(args.packet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(result["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
