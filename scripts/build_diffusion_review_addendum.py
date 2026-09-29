"""Build a small allowlisted review ZIP; never overwrite an existing handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "saia/__init__.py", "saia/cross_cluster_diffusion.py", "saia/source_text_view.py", "saia/coherence_review_comparison.py",
    "scripts/probe_cross_cluster_diffusion.py", "scripts/build_diffusion_review_addendum.py",
    "tests/test_cross_cluster_diffusion.py", "tests/test_cross_cluster_diffusion_probe.py",
    "tests/test_coherence_review_comparison.py",
    "config/cross-cluster-diffusion-gcn-regression-v1.json", "missions/ml-area-2017.json",
    "docs/cross-cluster-diffusion-regression-2026-09-28.md",
    "reports/generated/SAIA_cross_cluster_diffusion_review_2026-09-28.md",
    "outputs/cross-cluster-diffusion-gcn-2026-09-28-v1/input.json",
    "outputs/cross-cluster-diffusion-gcn-known-phrase-trace-2026-09-28.json",
    *["outputs/cross-cluster-diffusion-gcn-2026-09-28-v6/" + name for name in (
        "summary.json", "manifest.json", "input-reference.json",
        "raw-topic-proposals.json", "lineage-family-proposals.json")],
]


def build_archive(entries: dict[str, Path], output: Path, *, version: str,
                  production_changed: bool = False) -> dict:
    if (output.exists() or not output.parent.is_dir()
            or any(not path.is_file() or path.is_symlink() for path in entries.values())):
        raise ValueError("Missing source, symlink, output parent, or output already exists")
    if sum(path.stat().st_size for path in entries.values()) > 60 * 1024 * 1024:
        raise ValueError("Uncompressed addendum cap exceeded")
    manifest = {"version": version, "production_changed": production_changed,
                "files": [{"name": name, "bytes": path.stat().st_size,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                          for name, path in sorted(entries.items())]}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(entries.items()):
            archive.write(path, name)
        archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        for row in manifest["files"]:
            data = archive.read(row["name"])
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("ZIP manifest verification failure")
    return {"output": str(output), "files": len(entries) + 1,
            "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entries = {"README.md": ROOT / "handoff/SAIA_2026-09-28/DIFFUSION_ADDENDUM_START_HERE.md"}
    entries.update({"project/" + name: ROOT / name for name in FILES})
    print(json.dumps(build_archive(entries, args.output, version="diffusion-review-addendum-v1"), indent=2))


if __name__ == "__main__":
    main()
