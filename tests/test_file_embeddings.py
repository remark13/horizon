import pytest

from saia.file_embeddings import included_records
from saia.package_observations import payload_hash


def report(records):
    value = {
        'database_written': False,
        'models_used': False,
        'counts': {'confirmed_weak_signals': None},
        'records': records,
    }
    value['report_payload_sha256'] = payload_hash(value)
    return value


def record(identifier, decision='include'):
    return {
        'source_record_id': identifier,
        'title': 'Title',
        'abstract': 'Abstract',
        'payload_sha256': identifier,
        'quality': {'decision': decision},
    }


def test_file_generation_selection_reports_cap_without_silent_truncation():
    rows, capped = included_records(report([record('2'), record('1'), record('3')]), 2)
    assert [row['source_record_id'] for row in rows] == ['1', '2']
    assert capped is True


def test_file_generation_selects_all_available_include_records():
    rows, capped = included_records(report([record('2'), record('1', 'quarantine')]), 10)
    assert [row['source_record_id'] for row in rows] == ['2']
    assert capped is False


@pytest.mark.parametrize('limit', [0, 2001, 1.5])
def test_file_generation_limit_is_bounded(limit):
    with pytest.raises(ValueError, match='max_records'):
        included_records(report([record('1')]), limit)

