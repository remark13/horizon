import pytest

from saia.normalize import parse_openalex
from saia.source_identity import openalex_arxiv_identity
from saia.quality import decide


def payload(links, doi='https://doi.org/10.48550/arXiv.1609.03528'):
    return {'title': 'Graph neural network', 'doi': doi,
            'locations': [{'landing_page_url': 'https://arxiv.org/abs/' + i} for i in links]}


@pytest.mark.parametrize('links', [['1609.03528', '1708.06073'], ['1708.06073', '1609.03528']])
def test_arxiv_doi_cannot_be_overridden_by_location_order(links):
    row = payload(links)
    identity = openalex_arxiv_identity(row)
    assert identity['canonical_arxiv_id'] == '1609.03528'
    assert identity['conflicting_arxiv_links']
    assert ('arxiv', '1609.03528') in parse_openalex(row)['identifiers']
    assert ('arxiv', '1708.06073') not in parse_openalex(row)['identifiers']


def test_ambiguous_locations_without_arxiv_doi_are_not_an_arbitrary_identity():
    row = payload(['1609.03528', '1708.06073'], 'https://doi.org/10.1000/journal')
    assert openalex_arxiv_identity(row)['canonical_arxiv_id'] is None
    assert not any(k == 'arxiv' for k, v in parse_openalex(row)['identifiers'])


def test_unique_location_still_supports_journal_preprint_link():
    row = payload(['1609.03528', '1609.03528'], 'https://doi.org/10.1000/journal')
    result = openalex_arxiv_identity(row)
    assert result['canonical_arxiv_id'] == '1609.03528'
    assert not result['conflicting_arxiv_links']


def test_primary_doi_without_location_and_matching_link():
    assert openalex_arxiv_identity(payload([]))['canonical_arxiv_id'] == '1609.03528'
    assert not openalex_arxiv_identity(payload(['1609.03528']))['conflicting_arxiv_links']


def test_ids_doi_fallback_and_pdf_version_are_consistent():
    row = {'ids': {'doi': 'http://dx.doi.org/10.48550/arXiv.1609.03528'},
           'locations': [{'pdf_url': 'https://arxiv.org/pdf/1609.03528v2.pdf'}]}
    assert openalex_arxiv_identity(row)['canonical_arxiv_id'] == '1609.03528'
    assert not openalex_arxiv_identity(row)['conflicting_arxiv_links']


def test_link_ambiguity_flag_is_not_automatic_scientific_quarantine():
    result = decide(title='Graph neural network', abstract='A relevant method.',
                    publication_year=2016, date_is_imprecise=False, terms=['graph neural network'],
                    sources={'arxiv', 'openalex'}, openalex_payloads=[payload(['1708.06073'])])
    assert result.decision == 'include'
    assert result.flags['openalex_arxiv_link_ambiguity']


@pytest.mark.parametrize('url', ['https://evil-arxiv.org/abs/1609.03528',
                                 'https://example.org/?redirect=https://arxiv.org/abs/1609.03528',
                                 'https://arxiv.org/abs/1609.035280000'])
def test_noncanonical_link_does_not_establish_identity(url):
    row = {'locations': [{'landing_page_url': url}]}
    assert openalex_arxiv_identity(row)['canonical_arxiv_id'] is None
