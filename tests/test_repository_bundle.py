from importlib.util import module_from_spec, spec_from_file_location
import json
import hashlib
import io
import stat
from pathlib import Path
import zipfile

import pytest


def module(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / (name + '.py')
    spec = spec_from_file_location(name, path)
    value = module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_bundle_selection_does_not_include_private_corpus_or_working_artifacts():
    bundle = module('build_repository_bundle')
    files, excluded = bundle.collect(bundle.ROOT)
    assert set(bundle.REQUIRED) <= files.keys()
    assert 'tests/help_ui.cjs' in files and 'tests/horizon_dispatch_ui.cjs' in files
    assert '.env' not in files
    assert not any(name.startswith(('outputs/', 'work/', 'evaluation/', 'missions/', 'data/raw/', 'data/reference/priority_catalog/')) for name in files)
    assert not any(name.endswith(('.pdf', '.xlsx', '.dump', '.zip')) for name in files)
    assert not (set(bundle.EXCLUDED_RESEARCH_FILES) & files.keys())
    assert b'0.4.65' in files['README.md']
    assert files['SOURCE_SETUP.md'] == files['docs/repository/SOURCE_SETUP.md']
    assert bundle.PUBLIC_GUIDE in files


def test_secret_detection_reports_no_values():
    bundle = module('build_repository_bundle')
    value = b'not-a-real-credential-12345'
    report = bundle.inspect_files({'saia/example.py': value}, {value})
    assert report['blocking_findings'] == [{'path': 'saia/example.py', 'kind': 'possible_credential'}]
    assert value.decode() not in json.dumps(report)


def test_compressed_word_guide_is_scanned_for_credentials():
    bundle = module('build_repository_bundle')
    value = b'synthetic-document-credential'
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as document:
        document.writestr('word/document.xml', b'<document>' + value + b'</document>')
    report = bundle.inspect_files({bundle.PUBLIC_GUIDE: data.getvalue()}, {value})
    assert report['blocking_findings'] == [
        {'path': bundle.PUBLIC_GUIDE, 'kind': 'possible_credential'}
    ]
    assert value.decode() not in json.dumps(report)


def test_word_guide_rejects_embedded_documents():
    bundle = module('build_repository_bundle')
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as document:
        document.writestr('word/embeddings/object.bin', b'example')
    with pytest.raises(ValueError, match='embedded content'):
        bundle.inspect_files({bundle.PUBLIC_GUIDE: data.getvalue()}, set())


def test_word_secret_split_across_formatting_runs_is_detected():
    bundle = module('build_repository_bundle')
    value = b'synthetic-document-credential'
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as document:
        document.writestr('word/document.xml',
                         '<document><r><t>synthetic-document-</t></r>'
                         '<r><t>credential</t></r></document>')
    report = bundle.inspect_files({bundle.PUBLIC_GUIDE: data.getvalue()}, {value})
    assert report['blocking_findings'] == [
        {'path': bundle.PUBLIC_GUIDE, 'kind': 'possible_credential'}
    ]
    assert value.decode() not in json.dumps(report)


def test_archive_integrity_and_no_silent_overwrite(tmp_path, monkeypatch):
    bundle = module('build_repository_bundle')
    monkeypatch.setattr(bundle, 'collect', lambda root: ({'README.md': b'Example'}, []))
    monkeypatch.setattr(bundle, 'credential_values', lambda root: set())
    output = tmp_path / 'example.zip'
    report = bundle.build(tmp_path, output)
    assert report['files_verified'] == 1
    assert report['manifest_sha256'] == 'passed'
    assert report['public_publication_approved'] is True
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('horizon/MANIFEST.json'))
    assert manifest['purpose'] == 'public_competition_source_distribution'
    assert manifest['public_publication_approved'] is True
    assert manifest['license_selected'] is False
    with pytest.raises(FileExistsError):
        bundle.build(tmp_path, output)
    with zipfile.ZipFile(output, 'a') as archive:
        archive.writestr('horizon/extra.txt', 'unexpected')
    with pytest.raises(ValueError, match='inventory'):
        bundle.verify_archive(output)


def test_environment_initializer_generates_secret_without_overwriting(tmp_path):
    env = module('init_repository_env')
    (tmp_path / '.env.repository.example').write_text('# Local\nPOSTGRES_PASSWORD=\n')
    target = env.initialize(tmp_path)
    original = target.read_text()
    assert len(original.split('POSTGRES_PASSWORD=')[1].strip()) == 48
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        env.initialize(tmp_path)
    assert target.read_text() == original


def test_symlink_source_is_not_bundled(tmp_path):
    bundle = module('build_repository_bundle')
    outside = tmp_path / 'private.txt'
    outside.write_text('private')
    root = tmp_path / 'repository'
    root.mkdir()
    link = root / 'README.md'
    link.symlink_to(outside)
    assert bundle.safe_relative(root, link) is False


def test_consumer_key_is_included_in_known_credential_scan(tmp_path, monkeypatch):
    bundle = module('build_repository_bundle')
    value = 'synthetic-consumer-credential'
    monkeypatch.setenv('EPO_OPS_CONSUMER_KEY', value)
    secrets = bundle.credential_values(tmp_path)
    assert value.encode() in secrets
    assert bundle.inspect_files({'example.py': value.encode()}, secrets)['blocking_findings']


def test_archive_verifier_rejects_symlink_even_with_valid_manifest(tmp_path):
    bundle = module('build_repository_bundle')
    output = tmp_path / 'link.zip'
    raw = b'../../outside'
    link = zipfile.ZipInfo('horizon/link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    manifest = {'files': [{'path': 'link', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(link, raw)
        archive.writestr('horizon/MANIFEST.json', json.dumps(manifest))
    with pytest.raises(ValueError, match='regular files'):
        bundle.verify_archive(output)
