"""Public installation instructions must survive source-only repackaging."""
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOCS = ('README.md', 'ARCHITECTURE.md', 'DEPLOYMENT.md', 'SECURITY.md',
        'LICENSE_STATUS.md', 'THIRD_PARTY_NOTICES.md', 'VALIDATION.md',
        'SOURCE_SETUP.md')


def test_public_documentation_copies_match_and_relative_links_resolve():
    for name in DOCS:
        first = ROOT / name
        second = ROOT / 'docs/repository' / name
        assert first.read_bytes() == second.read_bytes(), name
        for path in (first, second):
            for link in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                if '://' in link or link.startswith('#'):
                    continue
                assert (path.parent / link.split('#', 1)[0]).exists(), (name, link)


def test_readme_explains_getting_code_configuration_and_safe_restart():
    content = (ROOT / 'README.md').read_text()
    assert 'git clone https://github.com/remark13/horizon.git' in content
    assert '--force-recreate app worker' in content
    assert 'SOURCE_SETUP.md' in content
    assert 'POSTGRES_PASSWORD' in content
    assert '127.0.0.1:8085/scout' in content


def test_clean_install_is_explicit_isolated_and_bounded():
    workflow = yaml.safe_load((ROOT / '.github/workflows/clean-install.yml').read_text())
    # PyYAML follows YAML 1.1 and interprets the GitHub key `on` as True.
    trigger = workflow.get('on', workflow.get(True))
    assert 'workflow_dispatch' in trigger
    job = workflow['jobs']['clean-amd64']
    assert job['timeout-minutes'] <= 30
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'build --pull --no-cache app' in commands
    assert '--no-build --wait' in commands
    assert 'import torch, bertopic, adapters' in commands
    assert '--env-file .env down --volumes' in commands
    for line in commands.splitlines():
        if line.lstrip().startswith('docker compose'):
            assert '-p horizon-clean-ci ' in line

