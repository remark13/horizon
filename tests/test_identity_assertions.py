import copy
import itertools

import pytest

from saia.normalize import canonical_identifiers, doi_conflicts, find_existing, identity_policy
from saia.publication_status import status_evidence
from saia.quality import decide


def claim(arxiv='1601.00001', title='A concrete method', author='Alice Smith', doi='10.1234/shared'):
    return {'title': title, 'identifiers': ([('arxiv', arxiv)] if arxiv else []) + [('doi', doi)],
            'authors': [{'display_name': author}] if author else []}


def test_different_arxiv_claimants_lose_canonical_doi_symmetrically_without_mutating_raw():
    rows = [claim(), claim('1602.00002')]
    original = copy.deepcopy(rows)
    for order in itertools.permutations(rows):
        conflicts = doi_conflicts(list(order))
        assert conflicts['10.1234/shared']['reasons'] == ['distinct_arxiv_ids']
        assert all(canonical_identifiers(r, conflicts)['_withheld_dois'] == ['10.1234/shared'] for r in rows)
        assert all(('doi', '10.1234/shared') not in canonical_identifiers(r, conflicts)['identifiers'] for r in rows)
    assert rows == original


def test_doi_collision_without_arxiv_needs_both_title_and_author_disagreement():
    a, b = claim(None), claim(None, 'A different task', 'Bob Jones')
    assert doi_conflicts([a, b])['10.1234/shared']['reasons'] == ['conflicting_titles_and_author_sets']
    assert doi_conflicts([a, claim(None, 'A different task')]) == {}
    assert doi_conflicts([a, claim(None, author='Bob Jones')]) == {}
    assert doi_conflicts([a, claim(None, 'A different task', None)]) == {}


def test_same_arxiv_and_compatible_metadata_preserve_doi_merge():
    assert doi_conflicts([claim(), claim()]) == {}
    assert canonical_identifiers(claim(), {})['identifiers'] == claim()['identifiers']


def test_arxiv_issued_doi_inconsistent_with_explicit_id_is_withheld():
    record = claim(doi='10.48550/arxiv.1701.12345')
    assert doi_conflicts([record])['10.48550/arxiv.1701.12345']['reasons'] == ['inconsistent_arxiv_doi']


class LookupCursor:
    def __init__(self, matches=None):
        self.matches = matches or {}
        self.seen = []
    def execute(self, sql, params):
        self.seen.append(params)
        self.result = (self.matches[(params[1], params[2])],) if (params[1], params[2]) in self.matches else None
    def fetchone(self):
        return self.result


def test_ambiguous_doi_cannot_reenter_via_title_fallback():
    conflicts = doi_conflicts([claim(), claim('1602.00002')])
    cursor = LookupCursor()
    assert find_existing(cursor, 1, canonical_identifiers(claim(), conflicts), conservative=True) == (None, None, None)
    assert cursor.seen == [(1, 'arxiv', '1601.00001')]


def test_strong_arxiv_identity_precedes_doi_and_oa_lookup():
    parsed = claim()
    parsed['identifiers'].reverse()
    cursor = LookupCursor({('arxiv', '1601.00001'): 42, ('doi', '10.1234/shared'): 99})
    assert find_existing(cursor, 1, parsed, conservative=True) == (42, 'arxiv_id_match', 'arxiv:1601.00001')
    assert len(cursor.seen) == 1


def test_oa_source_id_rule_is_not_mislabelled_as_a_doi_match():
    parsed = {'identifiers': [('openalex', 'W1')], '_withheld_dois': ['10.1234/shared']}
    assert find_existing(LookupCursor({('openalex', 'W1'): 7}), 1, parsed, conservative=True) == (7, 'openalex_source_identity_match', 'openalex:W1')


def test_new_and_explicit_legacy_passports_are_distinct():
    assert identity_policy('conservative')['version'] == 'canonical-identity-0.4.5'
    assert identity_policy('legacy')['mode'] == 'legacy'
    with pytest.raises(ValueError): identity_policy('unknown')


def quality(arxiv, oa=None, **kwargs):
    return decide(title='A concrete method', abstract='A concrete method.', publication_year=2016,
                  date_is_imprecise=False, terms=['concrete method'], sources={'arxiv'},
                  openalex_payloads=oa or [], arxiv_payloads=arxiv, **kwargs)


def test_single_withdrawn_deposit_quarantined_without_invented_effective_date():
    record = {'id': '1601.00001', 'arxiv_comment': 'This paper has been withdrawn by the author due to an error.',
              'updated': '2016-06-01', 'created': '2016-01-01'}
    result = quality([record], status_observed_at='2026-09-17T10:00:00+00:00')
    assert result.decision == 'quarantine'
    evidence = result.flags['deposit_status_evidence'][0]
    assert evidence['status_effective_date'] is None
    assert evidence['status_observed_at'] == '2026-09-17T10:00:00+00:00'
    assert evidence['evidence_fragment']


def test_withdrawal_does_not_automatically_invalidate_a_different_deposit():
    withdrawn = {'id': '1601.00001', 'arxiv_comment': 'This paper has been withdrawn by the author.'}
    unmarked = {'id': '1602.00002', 'arxiv_comment': ''}
    mixed = quality([withdrawn, unmarked])
    assert mixed.decision == 'include'
    assert mixed.flags['canonical_text_status_support_unknown'] is True
    assert quality([unmarked]).decision == 'include'


@pytest.mark.parametrize('comment', ['We cite a withdrawn paper.', 'This paper has not been withdrawn.', 'withdrawn', 'The authors of another article withdrew it.'])
def test_mentions_are_not_an_automatic_withdrawal(comment):
    assert status_evidence([{'id': 'x', 'arxiv_comment': comment}], [])[0]['status'] == 'not_reported'


def test_request_for_withdrawal_is_not_a_confirmed_status():
    record = {'id': '1601.00001', 'arxiv_comment': 'We would like to withdraw this submission.'}
    assert status_evidence([record], [])[0]['status'] == 'withdrawal_requested'
    result = quality([record])
    assert result.decision == 'include' and result.flags['publication_status_requires_review']


def test_openalex_retraction_requires_boolean_true_and_does_not_claim_a_date():
    assert status_evidence([], [{'id': 'W1', 'is_retracted': 'false'}])[0]['status'] == 'not_reported'
    assert quality([], [{'id': 'W1', 'is_retracted': True}]).decision == 'quarantine'


def test_doi_assertion_unknown_does_not_label_publication_noise():
    result = quality([{'id': '1601.00001'}], identity_conflicts=[{'asserted_doi': '10.1234/shared'}])
    assert result.decision == 'include'
    assert result.flags['canonical_publication_identity_unknown']
    assert any('число независимых исследований' in reason for reason in result.reasons)


def test_oai_reader_preserves_withdrawal_comment_for_status_detection(tmp_path):
    from saia.ingest import arxiv_oai_records
    path = tmp_path / 'fixture.xml'
    path.write_text('''<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords><record><metadata>
      <arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>1601.00001</id><created>2016-01-01</created>
      <title>A concrete method</title><abstract>A relevant method.</abstract><categories>cs.LG</categories>
      <comments>This paper has been withdrawn by the author due to an error.</comments>
      </arXiv></metadata></record></ListRecords></OAI-PMH>''')
    identifier, payload = list(arxiv_oai_records(path, {'query': {'arxiv_categories': ['cs.LG']}}))[0]
    assert identifier == '1601.00001' and payload['arxiv_comment']
    assert status_evidence([payload], [])[0]['status'] == 'withdrawn'
