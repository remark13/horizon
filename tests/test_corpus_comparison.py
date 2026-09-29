from copy import deepcopy

import pytest

from saia.corpus_comparison import compare, read_corpus


def work(identifier=1, arxiv='1601.00001', title='A result', abstract='An abstract', day='2016-01-01'):
    return {'work_id': identifier, 'arxiv_ids': [arxiv], 'title': title, 'abstract': abstract,
            'effective_date': day, 'sources': ['arxiv']}


def test_equal_identity_and_different_text_are_separate():
    old = [work()]
    new = [work(2, abstract='Changed abstract')]
    result = compare(old, new)
    assert result['shared_arxiv_ids'] == 1
    assert result['same_embedding_text_pairs'] == 0
    assert result['changed_embedding_text_pairs'] == 1


def test_even_whitespace_change_is_not_silent_vector_reuse():
    assert compare([work()], [work(2, title='A  result')])['changed_embedding_text_pairs'] == 1


def test_same_text_can_have_changed_dates_or_sources():
    new = work(2, day='2015-12-31')
    new['sources'] = ['arxiv', 'openalex']
    result = compare([work()], [new])
    assert result['same_embedding_text_pairs'] == 1
    assert result['changed_date_pairs'] == 1
    assert result['observations'][0]['target_sources'] == ['arxiv', 'openalex']


def test_missing_added_and_ambiguous_identity_are_not_negative_scientific_labels():
    old = [work(), work(4, '1601.00004')]
    new = [work(2), work(3), work(5, '1601.00005')]
    result = compare(old, new)
    assert result['missing_in_target'] == ['1601.00004']
    assert result['added_in_target'] == ['1601.00005']
    assert result['unambiguous_pairs'] == 0
    assert len(result['ambiguous_pairs']) == 1


def test_multiple_deposits_and_title_only_are_visible_not_mutated():
    old = work(1, abstract=None)
    old['arxiv_ids'].append('1601.00002')
    frozen = deepcopy(old)
    result = compare([old], [work(2, abstract=None)])
    assert old == frozen
    assert result['baseline_multi_arxiv_works'] == [1]
    assert result['observations'][0]['baseline_text']['text_source'] == 'title_only'


@pytest.mark.parametrize('row', [None, ('m', 'q', 'normalizing', 'normalize', {}, 'v'),
                                ('m', 'q', 'done', 'cluster', {}, 'v')])
def test_only_exact_finished_normalization_can_be_compared(row):
    class Cursor:
        def execute(self, *args): pass
        def fetchone(self): return row
    with pytest.raises(ValueError, match='завершённой нормализации'):
        read_corpus(Cursor(), 1)
