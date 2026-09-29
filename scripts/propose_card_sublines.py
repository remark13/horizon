"""Apply a frozen, bounded subline proposal plan to saved broad cards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.card_subline_proposals import propose
from saia.controlled_collection import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def run(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (config.get("version") != "card-title-subline-pilot-v1"
            or config.get("production_use") is not False
            or not 1 <= config.get("cards_to_process", 0) <= 100):
        raise ValueError("Unknown or unsafe card-subline plan")
    source_path = (ROOT / config["source"]).resolve()
    if (not source_path.is_relative_to(ROOT)
            or sha256_file(source_path) != config["source_sha256"]):
        raise ValueError("Frozen broad-card source differs")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    cards = source["cards"][:config["cards_to_process"]]
    if len(cards) != config["cards_to_process"]:
        raise ValueError("Not enough broad cards in frozen source")
    result = propose(cards, min_works=config["min_title_anchored_works"],
                     max_per_card=config["max_proposals_per_card"])
    result["config_sha256"] = sha256_file(config_path)
    result["source_sha256"] = config["source_sha256"]
    result["source_mission_id"] = source["mission_id"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new immutable output path")
    report = run(args.config)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"cards": report["cards_processed"],
                      "with_proposals": report["cards_with_subline_proposals"],
                      "sublines": report["proposed_sublines"]}))
