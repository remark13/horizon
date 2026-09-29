"""Command-line wrapper for the packaged focus-area diagnostic."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from saia.focus_area_diagnostic import run


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of", type=date.fromisoformat, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--pause-seconds", type=float, default=3.1)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.date_from >= arguments.as_of:
        raise SystemExit("date-from must be earlier than as-of")
    if not 1 <= arguments.limit <= 25:
        raise SystemExit("limit must be between 1 and 25")
    payload = run(
        arguments.base_url,
        arguments.date_from,
        arguments.as_of,
        arguments.limit,
        arguments.pause_seconds,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
