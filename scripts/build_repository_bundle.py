#!/usr/bin/env python3
"""Build a source-only repository bundle, never git-add the research workspace.

No push, history, database dump, model upload or external transmission.
The owner authorized this public competition release; licensing remains undecided.
Publication approval is not a legal clearance of third-party data or branding.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("saia", "scripts", "tools", "config", "migrations", "tests", "infra", ".github")
SUFFIXES = {".py", ".sql", ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".xml", ".csv", ".tsv", ".js", ".cjs", ".mjs", ".sh", ".html", ".css"}
ROOT_FILES = ("pyproject.toml", "compose.yaml", ".gitignore", ".dockerignore", ".env.example", ".env.repository.example")
REQUIRED = ("README.md", "compose.yaml", "infra/Dockerfile.repository", "pyproject.toml", "saia/api.py", "saia/server.py", "saia/help_content.py", "data/reference/public_signals/catalog.v6.json", "data/reference/public_signals/translations.ru.v1.json", "data/reference/public_signals/jrc.card-content.v1.json")
DENIED = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".agents", ".codex", "secrets"}
PRIVATE_CONFIG_PREFIXES = ("customer-", "priority-arxiv-", "priority-pilot-", "priority-catalog", "priority-holdout", "priority-source-", "priority-sources-")
EXCLUDED_RESEARCH_FILES = {
    'config/free-ru-short-application-coverage-groups-2026-09-27-v1.json',
    'config/free-ru-short-application-metadata-review-2026-09-27-v1.json',
    'config/free-ru-short-application-pilot-2026-09-27-v1.json',
    'config/goal-cross-domain-compound-pilot.v1.json',
    'config/goal-cross-domain-openalex-followup.v1.json',
    'config/goal-pilot-hypothesis-source-roles.v1.json',
    'config/goal-pilot-two-axis-hypothesis-types.v1.json',
    'config/goal-two-axis-type-holdout.v1.json',
    'config/ru-concept-holdout-16-v1.json',
    'config/ru-concept-plan-pilot.v1.json',
    'tests/test_ru_concept_acronym_guard.py',
    'config/broad-title-phrase-pilot-battery-materials-development-v2.json',
    'config/broad-title-phrase-pilot-battery-materials-holdout-v1.json',
    'config/broad-title-phrase-pilot-smr-holdout-v1.json',
    'config/openalex-title-phrase-pilot-smr-development-v1.json',
    'config/openalex-title-phrase-pilot-smr-development-v2.json',
    'config/openalex-title-phrase-pilot-smr-development-v3.json',
}
CANONICAL_DOCS = ('README.md', 'ARCHITECTURE.md', 'DEPLOYMENT.md', 'SECURITY.md',
                  'LICENSE_STATUS.md', 'THIRD_PARTY_NOTICES.md', 'VALIDATION.md',
                  'SOURCE_SETUP.md')
PUBLIC_GUIDE = 'docs/repository/Horizon_User_Guide_v0.4.65.docx'
REQUIRED = tuple(dict.fromkeys((*REQUIRED, *CANONICAL_DOCS)))
SECRET_PATTERNS = (
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"(?<![A-Za-z0-9])(?:ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9]{30,}",
    rb"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{50,}",
    rb"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{40,}",
    rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])",
)


def credential_values(root: Path) -> set[bytes]:
    """Known values only in memory. Never return values in reports."""
    values = dict(os.environ)
    for path in root.glob('.env*'):
        if path.name.endswith('.example') or path.is_symlink() or not path.is_file():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.partition('=')
            if sep:
                values[key.strip()] = value.strip().strip('\"\'')
    return {v.encode() for k, v in values.items()
            if re.search(r'(?:TOKEN|SECRET|PASSWORD|KEY)$', k)
            and len(v) >= 8 and not v.startswith(('replace-', 'your-', 'change-'))}


def safe_relative(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return not any(part in DENIED for part in relative.parts) and not any(
        parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent)


def collect(root: Path) -> tuple[dict[str, bytes], list[dict]]:
    root = root.resolve(strict=True)
    files, exclusions = {}, []
    candidates = [root / name for name in ROOT_FILES]
    for folder in SOURCE_DIRS:
        candidates.extend((root / folder).rglob('*'))
    for folder in ('data/reference/public_signals', 'data/reference/news_publishers'):
        candidates.extend((root / folder).glob('*.json'))
    for path in sorted(set(candidates)):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if not safe_relative(root, path):
            exclusions.append({'path': name, 'reason': 'private_directory_or_symlink'})
            continue
        if name in EXCLUDED_RESEARCH_FILES or (name.startswith('config/') and path.name.startswith(PRIVATE_CONFIG_PREFIXES)):
            exclusions.append({'path': name, 'reason': 'customer_derived_research_configuration'})
            continue
        if path.suffix not in SUFFIXES and path.name not in ROOT_FILES and not path.name.startswith('Dockerfile'):
            continue
        if path.stat().st_size > 5_000_000:
            raise ValueError(f'Source exceeds 5 MB review threshold: {name}')
        files[name] = path.read_bytes()
    # The repository has a concise entry point, not the working research diary.
    docs_root = root / 'docs/repository'
    if not docs_root.is_dir():
        docs_root = root  # also rebuilds from an extracted repository
    for name in CANONICAL_DOCS:
        path = docs_root / name
        if path.is_file() and safe_relative(root, path):
            files[name] = path.read_bytes()
            files['docs/repository/' + name] = path.read_bytes()
    # One explicitly reviewed manual, never arbitrary working Word files.
    guide = root / PUBLIC_GUIDE
    if guide.is_file() and safe_relative(root, guide):
        if guide.stat().st_size > 5_000_000:
            raise ValueError('Public guide exceeds 5 MB review threshold')
        files[PUBLIC_GUIDE] = guide.read_bytes()
    missing = sorted(set(REQUIRED) - files.keys())
    if missing:
        raise ValueError('Missing repository files: ' + ', '.join(missing))
    return files, exclusions


def inspect_files(files: dict[str, bytes], secrets: set[bytes]) -> dict:
    findings, personal_paths = [], []
    for name, raw in files.items():
        inspected = raw
        if name.endswith('.docx'):
            # Credentials in compressed OOXML must not bypass the source scan.
            with zipfile.ZipFile(io.BytesIO(raw)) as document:
                if sum(item.file_size for item in document.infolist()) > 20_000_000:
                    raise ValueError('Public guide uncompressed size exceeds review threshold')
                if any('vbaProject' in item.filename or '/embeddings/' in item.filename
                       for item in document.infolist()):
                    raise ValueError('Public guide contains active or embedded content')
                parts = [document.read(item) for item in document.namelist()
                         if item.endswith(('.xml', '.rels'))]
                # Formatting may split one visible credential across Word runs.
                visible = [''.join(ET.fromstring(part).itertext()).encode() for part in parts]
                inspected = b'\n'.join([*parts, *visible])
        if any(value in inspected for value in secrets) or any(re.search(pattern, inspected) for pattern in SECRET_PATTERNS):
            findings.append({'path': name, 'kind': 'possible_credential'})
        if re.search(rb'/Users/[^/\s]+/', inspected):
            personal_paths.append(name)
    return {'blocking_findings': findings, 'machine_path_review_files': personal_paths,
            'scan_scope': 'selected current files, known environment credentials and common token formats; not git history or a full security audit'}


def verify_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            kind = stat.S_IFMT(item.external_attr >> 16)
            if item.is_dir() or kind not in (0, stat.S_IFREG):
                raise ValueError('ZIP members must be regular files, not symlinks or devices')
        if archive.testzip():
            raise ValueError('ZIP CRC failed')
        names = archive.namelist()
        if len(names) != len(set(names)) or any(
            name.startswith('/') or '..' in Path(name).parts or '\\' in name for name in names
        ):
            raise ValueError('Unsafe or duplicate ZIP paths')
        manifest = json.loads(archive.read('horizon/MANIFEST.json'))
        expected = {'horizon/' + item['path'] for item in manifest['files']} | {'horizon/MANIFEST.json'}
        if set(names) != expected:
            raise ValueError('ZIP inventory differs from manifest')
        for item in manifest['files']:
            raw = archive.read('horizon/' + item['path'])
            if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('Manifest digest mismatch: ' + item['path'])
    return {'files_verified': len(manifest['files']), 'zip_crc': 'passed', 'manifest_sha256': 'passed'}


def build(root: Path, output: Path) -> dict:
    files, excluded = collect(root)
    scan = inspect_files(files, credential_values(root))
    if scan['blocking_findings']:
        # File names and categories only, never content or token excerpts.
        raise ValueError(json.dumps(scan['blocking_findings'], ensure_ascii=False))
    manifest = {'format': 'horizon-repository-bundle-v1', 'purpose': 'public_competition_source_distribution',
                'excluded_by_design': ['git history', 'database and expert submissions', 'customer source spreadsheets and derived catalog', 'evaluation packets', 'mission history', 'source PDFs', 'corpora and model weights', 'outputs and working reports', 'private environment files'],
                'excluded_source_files': excluded, 'security_scan': scan,
                'license_selected': False, 'public_publication_approved': True,
                'files': [{'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in sorted(files.items())]}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to replace an existing handoff silently.
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, raw in sorted(files.items()):
            archive.writestr('horizon/' + name, raw)
        archive.writestr('horizon/MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    verified = verify_archive(output)
    return {**verified, 'archive': str(output), 'bytes': output.stat().st_size,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'excluded_source_files': len(excluded), 'machine_path_review_files': len(scan['machine_path_review_files']),
            'public_publication_approved': True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    result = verify_archive(args.output) if args.verify else build(ROOT, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
