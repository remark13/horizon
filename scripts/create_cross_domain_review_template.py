"""Create an immutable, unfilled review template for the frozen packet."""

from __future__ import annotations

import argparse
from pathlib import Path

from saia.cross_domain_relevance_packet import json_bytes
from saia.cross_domain_relevance_review import blank_template


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Review template is immutable")
    template = blank_template(args.packet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json_bytes(template))
    print(f"Unlabelled review items: {len(template['labels'])}")


if __name__ == "__main__":
    main()
