from __future__ import annotations

import argparse
import json
from pathlib import Path

from saia.arxiv_trigram_index import promote_with_duplicate_guard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-index", type=Path, required=True)
    parser.add_argument("--mirror", type=Path, required=True)
    parser.add_argument("--duplicate-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = promote_with_duplicate_guard(
        base_index_dir=args.base_index, mirror_dir=args.mirror,
        duplicate_audit_path=args.duplicate_audit, output_dir=args.output)
    print(json.dumps({"output": str(args.output), "version": manifest["version"],
                      "index_bytes_hardlinked": manifest["file"]["bytes"],
                      "guard_bytes": manifest["duplicate_guard"]["bytes"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
