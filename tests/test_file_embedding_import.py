import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from saia.file_embedding_import import _selected_report_rows, sha256, verify_manifest
from saia.package_observations import payload_hash


def fixture(tmp_path):
    report = {
        'counts': {'quality_include_texts': 1},
        'records': [{
            'source_record_id': '2501.00001', 'title': 'Title', 'abstract': 'Abstract',
            'payload_sha256': 'a' * 64, 'quality': {'decision': 'include'},
        }],
    }
    report['report_payload_sha256'] = payload_hash(report)
    report_path = tmp_path / 'observations.json'
    report_path.write_text(json.dumps(report))

    generation = tmp_path / 'generation'
    generation.mkdir()
    parquet = generation / 'vectors.parquet'
    pq.write_table(pa.table({
        'source_record_id': ['2501.00001'],
        'input_payload_sha256': ['a' * 64],
        'embedding': pa.array([[1.0, 0.0]], type=pa.list_(pa.float32(), list_size=2)),
    }), parquet)
    manifest = {
        'status': 'passed', 'accuracy_evaluated': False,
        'input': {
            'selected_count': 1,
            'observation_report_bytes_sha256': sha256(report_path),
            'observation_report_payload_sha256': report['report_payload_sha256'],
        },
        'model': {'dimension': 2},
        'artifact': {'file': 'vectors.parquet', 'rows': 1, 'bytes_sha256': sha256(parquet)},
    }
    manifest['manifest_payload_sha256'] = payload_hash(manifest)
    (generation / 'manifest.json').write_text(json.dumps(manifest))
    return generation, report_path


def test_sealed_generation_and_observation_report_are_verified(tmp_path):
    generation, report_path = fixture(tmp_path)
    manifest, report, table = verify_manifest(generation, report_path)
    assert manifest['status'] == 'passed'
    assert report['counts']['quality_include_texts'] == 1
    assert table.num_rows == 1


def test_report_byte_tamper_is_rejected(tmp_path):
    generation, report_path = fixture(tmp_path)
    report_path.write_text(report_path.read_text() + '\n')
    with pytest.raises(ValueError, match='bytes differ'):
        verify_manifest(generation, report_path)


def test_selected_rows_require_reported_population():
    with pytest.raises(ValueError, match='population'):
        _selected_report_rows({'counts': {'quality_include_texts': 2}, 'records': []})
