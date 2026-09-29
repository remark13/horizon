import json

import pytest

from saia.package_observations import payload_hash
from saia.specter2_smoke import _adapter_effect, select_records


def report(records):
    value = {
        'database_written': False,
        'models_used': False,
        'counts': {'confirmed_weak_signals': None},
        'records': records,
    }
    value['report_payload_sha256'] = payload_hash(value)
    return value


def record(identifier, decision='include', abstract='Abstract'):
    return {
        'source_record_id': identifier,
        'title': 'Title',
        'abstract': abstract,
        'payload_sha256': identifier,
        'quality': {'decision': decision},
    }


def test_smoke_selection_is_bounded_sorted_and_include_only():
    value = report([
        record('3'),
        record('1', decision='quarantine'),
        record('2'),
        record('0', abstract=''),
    ])
    assert [row['source_record_id'] for row in select_records(value, 2)] == ['2', '3']


def test_smoke_rejects_changed_input_payload():
    value = report([record('1')])
    value['records'][0]['title'] = 'Changed'
    with pytest.raises(ValueError, match='payload hash'):
        select_records(value, 1)


@pytest.mark.parametrize('limit', [0, 101, 1.0])
def test_smoke_limit_is_strict(limit):
    with pytest.raises(ValueError, match='limit'):
        select_records(report([record('1')]), limit)


class FakeModel:
    active_adapters = 'Stack[proximity]'

    def set_active_adapters(self, value):
        self.active_adapters = value if value is not None else 'None'


class FakeEmbedder:
    model = FakeModel()

    def embed(self, texts):
        if 'proximity' in repr(self.model.active_adapters):
            return [[1.0, 2.0]]
        return [[0.0, 2.0]]


def test_adapter_probe_requires_and_restores_measurable_proximity_effect():
    result = _adapter_effect(FakeEmbedder(), 'text')
    assert result['proximity_effect_verified'] is True
    assert result['l2_difference_from_base'] == 1.0
    assert 'proximity' in repr(FakeEmbedder.model.active_adapters)
