from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.arxiv_trigram_index import build


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-shards", type=int)
    args = parser.parse_args()
    manifest = build(mirror_dir=args.mirror, output_dir=args.output,
                     max_shards=args.max_shards)
    print(json.dumps({"output": str(args.output), "counts": manifest["counts"],
                      "index_bytes": manifest["file"]["bytes"],
                      "complete": manifest["source"]["complete_pinned_inventory_indexed"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
