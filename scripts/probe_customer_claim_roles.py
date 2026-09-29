from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.customer_claim_roles import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--scope", choices=["pilot12", "holdout12", "all100"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Customer claim-role draft is immutable")
    report = run(args.catalog, args.pilot, scope=args.scope)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report["counts"], "gate_passed": report[
        "pilot_gate_passed_for_unreviewed_drafting_only"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
