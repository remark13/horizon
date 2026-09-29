from saia.research_identity import team_proxy, author_identity_disagreement
from saia.quality import decide


def test_first_author_changes_in_one_org_are_not_independent_teams():
    rows = [{'authors': ['A1', 'A2'], 'organisations': [1]},
            {'authors': ['A3', 'A4'], 'organisations': [1]}]
    assert team_proxy(rows, .5, .8)['teams'] == 1


def test_single_shared_author_does_not_collapse_whole_field():
    rows = [{'authors': ['A1', 'A2', 'A3'], 'organisations': [1]},
            {'authors': ['A1', 'A4', 'A5'], 'organisations': [2]}]
    assert team_proxy(rows, .5, .8)['teams'] == 2


def test_missing_reliable_identifiers_make_independence_unknown():
    assert team_proxy([{'authors': [], 'organisations': []}], .5, .8)['teams'] is None


def test_conflicting_identifiers_do_not_create_an_independent_team():
    assert team_proxy([{'authors': ['A1'], 'organisations': [1], 'identity_conflict': True}], .5, .8)['teams'] is None


def test_complete_byline_disagreement_not_order_or_missing_ids():
    def payload(ids):
        return {'authorships': [{'author': {'id': i}} for i in ids]}
    assert author_identity_disagreement([payload(['A1']), payload(['A2'])])
    assert not author_identity_disagreement([payload(['A1', 'A2']), payload(['A2', 'A1'])])
    assert not author_identity_disagreement([payload(['A1']), payload([None])])


def test_author_conflict_is_review_flag_not_scientific_quarantine():
    payloads = [{'authorships': [{'author': {'id': i}}]} for i in ['A1', 'A2']]
    result = decide(title='Graph neural network', abstract='A relevant method.',
                    publication_year=2016, date_is_imprecise=False, terms=['graph neural network'],
                    sources={'arxiv', 'openalex'}, openalex_payloads=payloads)
    assert result.decision == 'include'
    assert result.flags['author_identity_conflict']


def test_relevant_method_in_abstract_is_not_excluded_by_title():
    result = decide(title='Classification for a practical task',
                    abstract='We introduce a graph neural network for this task.',
                    publication_year=2016, date_is_imprecise=False,
                    terms=['graph neural network'], sources={'arxiv'}, openalex_payloads=[])
    assert result.decision == 'include'
    assert result.flags['abstract_only_query_match'] is True


def test_identical_title_without_identity_does_not_prove_duplicate():
    result = decide(title='Graph neural network', abstract='Independent result',
                    publication_year=2016, date_is_imprecise=False,
                    terms=['graph neural network'], sources={'arxiv'},
                    openalex_payloads=[], duplicate_rank=1)
    assert result.decision == 'include'
    assert result.flags['duplicate_title_rank'] == 1


def test_expert_term_year_is_review_flag_not_temporal_ground_truth():
    result = decide(title='Transformers for a task', abstract='A predecessor.',
                    publication_year=2014, date_is_imprecise=False,
                    terms=['transformers'], sources={'arxiv'}, openalex_payloads=[])
    assert result.decision == 'include'
    assert result.flags['anachronism_review']
