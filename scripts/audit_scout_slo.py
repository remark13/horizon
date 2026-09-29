from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.scout_slo_audit import audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8082")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Scout audit output is immutable")
    report = audit(state_path=args.state, base=args.base)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"elapsed_seconds": report[
        "from_user_request_to_completed_cards_seconds"],
                      "automatic_cards": report["automatic_cards"],
                      "known_momentum": report[
                          "cards_with_known_momentum_percentile"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
