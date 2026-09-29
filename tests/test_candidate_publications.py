from copy import deepcopy
from datetime import date

import pytest

from saia.candidate_publications import build_page, validate_page
from saia.candidates import composition_sha256


def page(rows=None, work_ids=None, limit=20, offset=0):
    return build_page(mission_id='m', score_run_id=9, candidate_id=2,
                      work_ids=work_ids or [11], rows=rows if rows is not None else [{
                          'work_id': 11, 'title': 'A', 'effective_date': date(2025, 1, 1),
                          'publication_date': None, 'type': 'preprint', 'language': 'en',
                          'identifiers': [{'kind': 'arxiv', 'value': '2501.00001'}],
                          'abstract': 'Not redistributed',
                      }], limit=limit, offset=offset)


def test_metadata_is_exact_and_does_not_claim_primary_result():
    result = page()
    assert result['composition_sha256'] == composition_sha256([11])
    assert result['works'][0]['published_at'] == '2025-01-01'
    assert result['works'][0]['sources'][0]['url'] == 'https://arxiv.org/abs/2501.00001'
    assert result['works'][0]['primary_result_verified'] is None
    assert 'abstract' not in result['works'][0]
    assert result['ranking_changed'] is False
    assert result['single_technology_verified'] is None


def test_pagination_preserves_full_composition_hash_and_has_terminal_page():
    result = page(work_ids=[11, 12], limit=1)
    assert result['total'] == 2 and result['next_offset'] == 1
    terminal = page(rows=[], work_ids=[11, 12], limit=1, offset=2)
    assert terminal['next_offset'] is None and terminal['works'] == []
    assert terminal['composition_sha256'] == result['composition_sha256']


@pytest.mark.parametrize('limit,offset', [(0, 0), (101, 0), (True, 0), (20, -1), (20, True)])
def test_invalid_page_bounds_are_rejected(limit, offset):
    with pytest.raises(ValueError):
        validate_page(limit, offset)


def test_unrelated_duplicate_or_short_page_is_rejected_without_mutating_input():
    rows = [{'work_id': 13, 'title': 'wrong', 'effective_date': '2025-01-01'}]
    before = deepcopy(rows)
    with pytest.raises(ValueError, match='постороннюю'):
        page(rows=rows)
    assert rows == before
    with pytest.raises(ValueError, match='составу'):
        page(rows=[], work_ids=[11])
    with pytest.raises(ValueError, match='повторённую'):
        page(rows=rows*2, work_ids=[13, 14])


def test_publication_endpoint_passes_exact_scope_and_bounds(monkeypatch):
    from fastapi.testclient import TestClient
    from saia.api import app
    import saia.candidate_publications as publications

    seen = []
    def fake(*args):
        seen.append(args)
        if args[0] != 'm':
            raise ValueError('Карточка отсутствует')
        return {'total': 1, 'works': []}
    monkeypatch.setattr(publications, 'for_candidate', fake)
    client = TestClient(app)
    assert client.get('/signals/m/2/publications?score_run_id=9&limit=10&offset=1').status_code == 200
    assert seen == [('m', 9, 2, 10, 1)]
    assert client.get('/signals/other/2/publications?score_run_id=9').status_code == 404
    assert client.get('/signals/m/2/publications?score_run_id=9&limit=101').status_code == 422
    assert client.get('/signals/m/2/publications').status_code == 422
