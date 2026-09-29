import copy

import pytest

from saia.evaluation_catalog import load, validate
from saia.hybrid import digest
from saia.reference_audit import parse, revalidate, validate_report


HTML = '''<meta name="citation_arxiv_id" content="1511.06279">
<meta name="citation_title" content="Neural Programmer-Interpreters">
<meta name="citation_date" content="2015/11/19">
<meta name="citation_online_date" content="2016/02/29">
<meta name="citation_abstract" content="Do not store the abstract">
<td class="tablecell subjects"><span>Machine Learning (cs.LG)</span>; Neural and Evolutionary Computing (cs.NE)</td>'''


def fixture():
    catalog = {'reference_titles': {'1511.06279': 'Neural Programmer-Interpreters'},
               'cases': [{'case_id': 'npi', 'arxiv_ids': ['1511.06279'], 'as_of_date': '2017-01-01'}]}
    record = parse(HTML, '1511.06279', catalog['reference_titles']['1511.06279'])
    record.update(request_url='https://arxiv.org/abs/1511.06279', http_status=200, response_sha256='a' * 64)
    return catalog, {'catalog_hash': digest(catalog), 'records': [record]}


def test_metadata_identity_and_categories_not_abstract_are_saved():
    catalog, report = fixture()
    validate_report(report, catalog)
    record = report['records'][0]
    assert record['primary_category'] == 'cs.LG' and record['categories'] == ['cs.LG', 'cs.NE']
    assert record['first_submission'] == '2015-11-19' and record['current_revision'] == '2016-02-29'
    assert 'abstract' not in record and 'Do not store' not in str(record)


def test_wrong_id_is_not_verified_by_a_plausible_title():
    assert parse(HTML, '1511.05179', 'Neural Programmer-Interpreters')['status'] == 'identity_mismatch'


def test_wrong_title_is_not_verified_by_matching_id():
    assert parse(HTML, '1511.06279', 'Shape Dependence of Entanglement Entropy')['status'] == 'identity_mismatch'


@pytest.mark.parametrize('change', ['title', 'status', 'catalog_hash', 'duplicates', 'url', 'http'])
def test_validation_rejects_unverified_or_mismatched_evidence(change):
    catalog, report = fixture()
    if change == 'title': report['records'][0]['title'] = 'Other research'
    if change == 'status': report['records'][0]['status'] = 'source_or_parse_error'
    if change == 'catalog_hash': report['catalog_hash'] = 'old'
    if change == 'duplicates': report['records'].append(copy.deepcopy(report['records'][0]))
    if change == 'url': report['records'][0]['request_url'] = 'https://example.com'
    if change == 'http': report['records'][0]['http_status'] = 429
    with pytest.raises(ValueError): validate_report(report, catalog)


def test_catalog_reference_titles_must_cover_exact_ids():
    catalog = load()
    catalog['reference_titles']['1511.05179'] = 'Wrong work'
    with pytest.raises(ValueError, match='exactly cover'): validate(catalog)


def test_future_seed_is_not_allowed_by_verified_current_metadata():
    catalog, report = fixture()
    catalog['cases'][0]['as_of_date'] = '2015-11-19'
    report['catalog_hash'] = digest(catalog)
    with pytest.raises(ValueError, match='before its cutoff'): validate_report(report, catalog)


def test_current_catalog_corrects_npi_without_changing_the_generator_dictionary():
    catalog = load()
    case = next(c for c in catalog['cases'] if c['case_id'] == 'dev-programming')
    assert case['arxiv_ids'] == ['1511.06279', '1511.04834']
    assert '1511.05179' not in catalog['reference_titles']


def test_evaluator_requires_frozen_reference_audit_before_database_access(monkeypatch):
    from saia import evaluation_catalog as module
    def forbidden(): pytest.fail('No DB query before identity validation')
    monkeypatch.setattr(module.db, 'connect', forbidden)
    with pytest.raises(ValueError, match='Reference audit'):
        module.evaluate({}, load())


def test_revalidation_rechecks_explicit_titles_but_keeps_original_metadata(monkeypatch):
    from saia import reference_audit as module
    catalog, report = fixture()
    report['catalog_hash'] = 'previous-catalog'
    report['records'][0]['status'] = 'identity_mismatch'
    original = copy.deepcopy(report)
    def forbidden(*args, **kwargs): pytest.fail('Revalidation must not refetch')
    monkeypatch.setattr(module.httpx, 'Client', forbidden)
    checked = revalidate(report, catalog)
    assert report == original
    assert checked['parent_audit_hash'] == digest(original)
    assert checked['records'][0]['response_sha256'] == original['records'][0]['response_sha256']
    assert checked['records'][0]['previous_identity_status'] == 'identity_mismatch'


def test_revalidation_cannot_accept_a_still_incorrect_explicit_title():
    catalog, report = fixture()
    catalog['reference_titles']['1511.06279'] = 'Different research'
    with pytest.raises(ValueError, match='Unverified'): revalidate(report, catalog)


@pytest.mark.parametrize('change', ['chronology', 'categories', 'digest'])
def test_saved_metadata_must_have_valid_chronology_categories_and_response_digest(change):
    catalog, report = fixture()
    if change == 'chronology': report['records'][0]['current_revision'] = '2010-01-01'
    if change == 'categories': report['records'][0]['categories'] = []
    if change == 'digest': report['records'][0]['response_sha256'] = 'fixture'
    with pytest.raises(ValueError): validate_report(report, catalog)
