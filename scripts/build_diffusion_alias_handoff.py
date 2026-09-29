"""Allowlisted, standalone analytical handoff; not a full application export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.build_diffusion_review_addendum import build_archive


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "saia/__init__.py", "saia/cross_cluster_diffusion.py", "saia/scientific_aliases.py",
    "saia/emergence_priority.py", "saia/source_text_view.py",
    "scripts/probe_diffusion_alias_ranking.py", "scripts/probe_cross_cluster_diffusion.py",
    "scripts/build_diffusion_review_addendum.py", "scripts/build_diffusion_alias_handoff.py",
    "tests/test_cross_cluster_diffusion.py", "tests/test_cross_cluster_diffusion_probe.py",
    "tests/test_scientific_aliases.py", "tests/test_emergence_priority.py",
    "tests/test_source_text_view.py", "tests/test_diffusion_alias_ranking_probe.py",
    "config/diffusion-alias-ranking-experiment-v1.json", "config/diffusion-alias-ranking-controls-v1.json",
    "config/diffusion-source-text-audit-v1.json", "config/cross-cluster-diffusion-gcn-regression-v1.json",
    "docs/diffusion-alias-ranking-2026-09-28.md", "docs/cross-cluster-diffusion-regression-2026-09-28.md",
    "docs/goal-next-scope-checkpoint-2026-09-28.md",
    "reports/generated/SAIA_GAN_metadata_mismatch_2026-09-28.json",
    "outputs/cross-cluster-diffusion-gcn-2026-09-28-v1/input.json",
    "outputs/diffusion-alias-ranking-2026-09-28-v1/comparison.json",
    "outputs/diffusion-source-text-audit-2026-09-28-v2-docker/comparison.json",
    *["outputs/diffusion-source-text-audit-2026-09-28-v1/" + name for name in (
        "comparison.json", "input-reference.json", "exact-original.json", "exact-emergence.json",
        "normalized-original.json", "normalized-emergence.json")],
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    entries = {"README.md": ROOT / "handoff/SAIA_2026-09-28/ALIAS_RANKING_START_HERE.md"}
    entries.update({"project/" + name: ROOT / name for name in FILES})
    print(json.dumps(build_archive(entries, args.output, version="diffusion-alias-ranking-addendum-v1"), indent=2))


if __name__ == "__main__":
    main()
