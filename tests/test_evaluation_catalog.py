import copy
from datetime import date
from pathlib import Path

import pytest

from saia.evaluation_catalog import load, selected_cases, summary, validate, render


def test_catalog_is_24_cases_but_not_24_independent_trends_or_gold():
    report = summary(load())
    assert report['cases'] == 24 and report['families'] == 16
    assert report['splits'] == {'development': 12, 'reserved': 12}
    assert report['weak_signal_gold_labels'] == 0 and report['training_readiness'] == 'not_ready'
    assert report['kinds']['empirical_observation_negative'] == 8


def test_reserved_cases_are_not_evaluated_by_default_or_for_tuning():
    catalog = load()
    assert len(selected_cases(catalog, 'development')) == 12
    with pytest.raises(ValueError, match='Резерв'):
        selected_cases(catalog, 'reserved')


def test_same_family_cannot_cross_splits_even_with_different_articles():
    catalog = copy.deepcopy(load())
    catalog['cases'][-1]['family_id'] = 'gan'
    with pytest.raises(ValueError, match='Семейство'):
        validate(catalog)


def test_same_publication_cannot_cross_splits_under_different_family_name():
    catalog = copy.deepcopy(load())
    catalog['cases'][4]['arxiv_ids'] = ['1406.2661']
    with pytest.raises(ValueError, match='Связанная работа'):
        validate(catalog)


def test_future_or_market_truth_cannot_be_silently_added_to_provisional_cases():
    catalog = copy.deepcopy(load())
    catalog['cases'][0]['future_growth_label'] = True
    with pytest.raises(ValueError, match='целевой метки'):
        validate(catalog)


def test_duplicate_rule_requires_identifiers_of_same_paper():
    catalog = copy.deepcopy(load())
    catalog['cases'][-1]['alternate_identifiers'] = ['10.48550/arxiv.9999.99999']
    with pytest.raises(ValueError, match='DOI'):
        validate(catalog)


def test_negative_observation_is_not_invented_technology_failure():
    catalog = copy.deepcopy(load())
    catalog['cases'][-1]['negative_rule'] = 'technology_failed'
    with pytest.raises(ValueError, match='Отрицательный'):
        validate(catalog)


def test_report_keeps_inline_sources_and_limitations():
    text = render(load())
    assert 'https://arxiv.org/abs/1610.02527' in text
    assert '1610.02554' not in text
    assert 'не утверждение, что технология' in text


class FakeDatabase:
    def __init__(self, rows):
        self.rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def cursor(self):
        return self
    def execute(self, *_):
        pass
    def fetchall(self):
        return self.rows


def test_evaluation_never_regenerates_detector_and_gates_insufficient_targets(monkeypatch):
    from saia import evaluation_catalog as module
    catalog = load(Path(__file__).resolve().parents[1] / 'evaluation' / 'cases.v0.4.yaml')
    catalog['cases'] = [catalog['cases'][0]]
    fake = FakeDatabase([
        ('arxiv', '1406.2661', 1, date(2014, 6, 10), 'include', [], []),
        ('arxiv', '1511.06434', 2, date(2015, 11, 19), 'quarantine', ['late_revision'], ['fixture']),
    ])
    monkeypatch.setattr(module.db, 'connect', lambda: fake)
    def never(*_):
        pytest.fail('Frozen evaluator must not call detector')
    monkeypatch.setattr(module.hybrid, 'analyze', never)
    snapshot = {'snapshot_id': 'fixture', 'provenance': {'normalize_run_id': 1,
                'quality_generation_id': 2}, 'eligible_work_ids': [1],
                'as_of_date': '2017-01-01', 'candidates': []}
    report = module.evaluate(snapshot, catalog)
    row = report['rows'][0]
    assert row['status'] == 'insufficient_eligible_targets' and row['recovery'] is None
    assert row['mapped_but_ineligible_reference_ids'] == ['1511.06434']
    assert row['observations']['1511.06434'][0]['quality_flags'] == ['late_revision']
    assert report['precision_at_15'] is None and report['heldout'] is False


def test_other_cutoff_is_not_counted_as_detection_failure(monkeypatch):
    from saia import evaluation_catalog as module
    catalog = load(Path(__file__).resolve().parents[1] / 'evaluation' / 'cases.v0.4.yaml')
    catalog['cases'] = [catalog['cases'][1]]
    monkeypatch.setattr(module.db, 'connect', lambda: FakeDatabase([]))
    snapshot = {'snapshot_id': 'fixture', 'provenance': {'normalize_run_id': 1,
                'quality_generation_id': 2}, 'eligible_work_ids': [],
                'as_of_date': '2017-01-01', 'candidates': []}
    row = module.evaluate(snapshot, catalog)['rows'][0]
    assert row['status'] == 'different_historical_cutoff' and row['recovery'] is None
