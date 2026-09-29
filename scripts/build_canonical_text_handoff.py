"""Allowlisted text-source review addendum with standalone offline audit."""

import argparse
import json
from pathlib import Path

from scripts.build_diffusion_review_addendum import build_archive


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'saia/__init__.py', 'saia/canonical_text.py', 'saia/arxiv_metadata.py',
    'saia/normalize.py', 'saia/full_analysis.py', 'migrations/058_work_text_provenance.sql',
    'scripts/audit_canonical_text.py', 'scripts/build_canonical_text_handoff.py',
    'scripts/build_diffusion_review_addendum.py',
    'tests/test_canonical_text.py', 'tests/test_canonical_text_audit.py',
    'tests/test_canonical_text_cache.py', 'tests/test_integration.py',
    'config/canonical-text-audit-v1.json', 'CURRENT_STATE.md',
    'docs/canonical-text-selection-audit-2026-09-28.md',
    'docs/diffusion-alias-ranking-2026-09-28.md',
    'docs/goal-next-scope-checkpoint-2026-09-28.md',
    'reports/generated/SAIA_cross_cluster_diffusion_review_2026-09-28.md',
    'reports/generated/SAIA_cross_cluster_diffusion_review_followup_2026-09-28.md',
    'reports/generated/SAIA_GAN_metadata_source_trace_2026-09-28.json',
    *['outputs/canonical-text-audit-2026-09-28-v2/' + name for name in
      ('input.json', 'audit.json', 'validation.json')],
    'outputs/canonical-text-audit-2026-09-28-v3-host/validation.json',
    'outputs/canonical-text-audit-2026-09-28-v4-docker/validation.json',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    entries = {'README.md': ROOT / 'handoff/SAIA_2026-09-28/CANONICAL_TEXT_START_HERE.md'}
    entries.update({'project/' + name: ROOT / name for name in FILES})
    print(json.dumps(build_archive(entries, args.output, version='canonical-text-review-addendum-v1',
                                  production_changed=True), indent=2))


if __name__ == '__main__':
    main()
