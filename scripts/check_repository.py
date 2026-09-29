#!/usr/bin/env python3
"""Portable release checks. Historical research tests require separate data."""
from pathlib import Path
import subprocess
import sys

TESTS = (
    'tests/test_api.py', 'tests/test_scoring.py', 'tests/test_contracts.py',
    'tests/test_help.py', 'tests/test_horizon_dispatch_ui.py',
    'tests/test_public_signals.py', 'tests/test_public_signal_i18n.py',
    'tests/test_scout_public_signals.py', 'tests/test_jrc_card_content.py',
    'tests/test_demo_access.py', 'tests/test_repository_compose.py',
    'tests/test_repository_bundle.py', 'tests/test_multi_branch_discovery.py',
    'tests/test_universal_materialization.py',
    'tests/test_runs.py', 'tests/test_vertical_slice.py',
    'tests/test_public_signal_requests.py', 'tests/test_integration.py',
)


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', *TESTS], cwd=root))
