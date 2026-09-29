"""No-model, source-sentence relevance diagnostic on frozen papers.

The rule is intentionally lexical and cannot verify a paper's actual own
contribution. It is compared against developer labels only after prediction.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from saia.arxiv_metadata import ORTHOGRAPHIC_MATCHING_VERSION, _matches_phrase
from saia.controlled_collection import sha256_file
from scripts.probe_bas_article_facets_v2 import own_claim_ids
from scripts.probe_grounded_task_facets_v3 import source_spans


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "evaluation/bas-title-anchor-paper-blind-review-2026-09-27-v2.json"
EXPECTED_PACKET_SHA = "9483d6e4fc5838f879d279ffc4785bdebd8a797741873a57f7a1a89c91ee9fb2"
VERSION = "title-anchor-own-claim-lexical-probe-v1"
REVIEW_MARKERS = ("survey", "review", "overview", "bibliometric", "meta-analysis")


def predict(case: dict) -> dict:
    spans = source_spans(case["paper"])
    phrase = case["proposed_line_en"]
    title = case["paper"]["title"]
    if any(_matches_phrase(title, marker, ORTHOGRAPHIC_MATCHING_VERSION)
           for marker in REVIEW_MARKERS):
        return {"own_result": "no", "basis": "review_title",
                "source_span_ids": ["T"]}
    own_ids = own_claim_ids(spans)
    if not own_ids:
        return {"own_result": "unclear", "basis": "no_explicit_own_claim_cue",
                "source_span_ids": []}
    matching = [identifier for identifier in own_ids if _matches_phrase(
        spans[identifier], phrase, ORTHOGRAPHIC_MATCHING_VERSION)]
    return {"own_result": "yes" if matching else "no",
            "basis": "own_claim_sentence_contains_phrase" if matching else
                     "own_claim_sentences_do_not_contain_phrase",
            "source_span_ids": matching or own_ids[:2]}


def run(packet: dict) -> dict:
    if packet.get("version") != "title-anchor-paper-review-v2":
        raise ValueError("Unexpected blind packet")
    rows = [{"case_id": case["case_id"],
             "proposed_line_en": case["proposed_line_en"],
             "prediction": predict(case)} for case in packet["cases"]]
    return {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "blind_packet_sha256": EXPECTED_PACKET_SHA,
            "rows": rows, "case_count": len(rows),
            "production_changed": False,
            "semantic_accuracy_not_proven_by_sentence_cooccurrence": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if sha256_file(PACKET) != EXPECTED_PACKET_SHA:
        raise ValueError("Blind packet changed")
    result = run(json.loads(PACKET.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case_count": result["case_count"]}))


if __name__ == "__main__":
    main()
