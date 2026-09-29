from saia.openalex_sample_audit import abstract_text, summarize


def row(identifier, day, doi=None, abstract=True, location=None):
    payload = {
        'id': f'https://openalex.org/W{identifier}',
        'title': 'Active learning for a useful task',
        'publication_date': day,
        'doi': doi,
        'authorships': [{'author': {'id': 'https://openalex.org/A1'}}],
        'counts_by_year': [{'year': 2025, 'cited_by_count': 1}],
        'abstract_inverted_index': {'sample': [0], 'selection': [1]} if abstract else None,
        'locations': [],
        'ids': {},
    }
    if location:
        payload['locations'].append({'landing_page_url': f'https://arxiv.org/abs/{location}'})
    return payload


def test_abstract_text_reconstructs_positions():
    assert abstract_text({'abstract_inverted_index': {'world': [1], 'hello': [0]}}) == 'hello world'


def test_summary_separates_sample_coverage_and_bibliographic_overlap():
    rows = [
        row(1, '2025-01-02', 'https://doi.org/10.48550/arxiv.2501.00001'),
        row(2, '2025-02-03', location='2502.00002'),
    ]
    pages = [
        {'source_reported_count': 10},
        {'source_reported_count': 20},
    ]
    result = summarize(rows, {'2501.00001'}, ['active learning', 'sample selection'], pages)
    assert result['sample_records'] == 2
    assert result['source_reported_records_in_disjoint_months'] == 30
    assert result['sample_fraction_of_reported_query_results'] == 2 / 30
    assert result['rows_with_literal_query_phrase'] == 2
    assert result['literal_phrase_counts_nonexclusive'] == {
        'active learning': 2, 'sample selection': 2,
    }
    assert result['unique_resolved_arxiv_ids'] == 2
    assert result['resolved_arxiv_ids_in_phrase_scope'] == 1
    assert result['resolved_arxiv_ids_outside_phrase_scope'] == 1


def test_summary_rejects_duplicate_openalex_ids():
    rows = [row(1, '2025-01-02'), row(1, '2025-01-03')]
    try:
        summarize(rows, set(), ['active learning'], [{'source_reported_count': 2}])
    except ValueError as error:
        assert 'unique' in str(error)
    else:
        raise AssertionError('duplicate IDs must fail')
