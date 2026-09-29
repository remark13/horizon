import pytest

from saia.candidate_evidence_audit import build_packet


def fixture():
    snapshot = {'snapshot_id': 'frozen', 'provenance': {'mission_id': 'mission', 'normalize_run_id': 7},
                'candidates': [{'candidate_id': 'c', 'label': 'topic', 'channels': ['semantic'],
                                'work_ids': [1, 2], 'publication_series': {'points': []},
                                'first_observed_in_corpus': '2016-01-01'}]}
    works = [{'work_id': i, 'mission_id': 'mission', 'run_id': 7,
              'effective_date': '2016-01-01', 'title': 'paper'} for i in [1, 2]]
    return snapshot, works


def test_packet_keeps_whole_roster_and_does_not_generate_gold():
    snapshot, works = fixture()
    result = build_packet(snapshot, ['c'], works)
    assert result['unique_works'] == 2
    case = result['cases'][0]
    assert len(case['works']) == 2 and case['suggested_reading_work_ids'] == [1, 2]
    assert case['analytical_label'] is None and case['independent_gold'] is False
    assert result['accuracy_evaluated'] is False


@pytest.mark.parametrize('ids', [[], ['c', 'c'], ['other']])
def test_explicit_distinct_snapshot_scoped_selection(ids):
    snapshot, works = fixture()
    with pytest.raises(ValueError):
        build_packet(snapshot, ids, works)


def test_missing_work_cannot_silently_shorten_candidate():
    snapshot, works = fixture()
    with pytest.raises(ValueError, match='exactly'):
        build_packet(snapshot, ['c'], works[:1])


def test_foreign_generation_is_not_current_evidence():
    snapshot, works = fixture()
    works[0]['run_id'] = 8
    with pytest.raises(ValueError, match='frozen input'):
        build_packet(snapshot, ['c'], works)
