from datetime import date
import copy

import pytest
import yaml

from saia.terminology import POLICY_PATH, PublicationText, generate, phrase_spans


def doc(i, year, title, abstract=None):
    return PublicationText(i, date(year, 1, 2), title, abstract)


def test_future_text_does_not_create_dictionary_or_denominator():
    result = generate([doc(1, 2016, 'Graph neural networks'),
                       doc(2, 2019, 'Graph neural networks quantum magic')], date(2017, 1, 1))
    assert result['corpus_works'] == 1
    assert result['candidates'] == []
    assert not any('quantum' in c['phrase'] for c in result['low_support_review'])


def test_support_counts_documents_not_repetitions():
    result = generate([doc(1, 2016, 'Graph neural networks', 'Graph neural networks ' * 20)],
                      date(2017, 1, 1))
    phrase = next(c for c in result['low_support_review'] if c['phrase'] == 'graph neural networks')
    assert phrase['document_support'] == 1
    assert len(phrase['contexts']) == 1


def test_offsets_recover_original_unicode_fragment():
    title = 'Обучение: Графовые нейронные сети помогают анализу'
    result = generate([doc(1, 2015, title), doc(2, 2016, title)], date(2017, 1, 1))
    phrase = next(c for c in result['candidates'] if c['phrase'] == 'графовые нейронные сети')
    context = phrase['contexts'][0]
    assert title[context['start']:context['end']] == context['matched_text']
    assert context['matched_text'] == 'Графовые нейронные сети'


def test_phrases_do_not_cross_sentences_stopwords_or_numbers():
    policy = yaml.safe_load(POLICY_PATH.read_text())
    spans = phrase_spans('graph. neural and networks 2020 deep learning', policy)
    phrases = [s[0] for s in spans]
    assert 'deep learning' in phrases
    assert 'graph neural' not in phrases
    assert 'neural networks' not in phrases
    assert 'networks deep' not in phrases


def test_missing_coverage_never_asserts_growth():
    docs = [doc(i, 2012 + i, 'Graph neural networks') for i in range(1, 5)]
    result = generate(docs, date(2017, 1, 1))
    series = result['candidates'][0]['publication_series']
    assert series['share_slope_per_window'] is None
    assert series['direction'] == 'not_established'


def test_zero_windows_are_preserved():
    result = generate([doc(1, 2013, 'Graph neural networks'),
                       doc(2, 2016, 'Graph neural networks')], date(2017, 1, 1))
    series = next(c for c in result['candidates'] if c['phrase'] == 'graph neural networks')['publication_series']
    assert [p['topic_works'] for p in series['points']] == [1, 0, 0, 1]


def test_order_is_reproducible_and_hash_changes_with_text():
    docs = [doc(1, 2015, 'Graph neural networks'), doc(2, 2016, 'Graph neural networks')]
    a = generate(docs, date(2017, 1, 1))
    assert a == generate(list(reversed(docs)), date(2017, 1, 1))
    b = generate([docs[0], doc(2, 2016, 'Graph neural networks improved')], date(2017, 1, 1))
    assert a['input_text_hash'] != b['input_text_hash']


def test_duplicate_canonical_work_is_rejected():
    with pytest.raises(ValueError, match='несколько раз'):
        generate([doc(1, 2016, 'Graph neural networks')] * 2, date(2017, 1, 1))


def test_policy_is_checked():
    policy = copy.deepcopy(yaml.safe_load(POLICY_PATH.read_text()))
    policy['ngram_lengths'] = [1]
    with pytest.raises(ValueError, match='паспорт'):
        generate([], date(2017, 1, 1), policy=policy)


def test_empty_corpus_is_explicit_not_a_signal():
    result = generate([], date(2017, 1, 1))
    assert result['corpus_works'] == 0
    assert result['candidates'] == result['low_support_review'] == []


def test_excessive_corpus_is_rejected_not_silently_sampled():
    policy = yaml.safe_load(POLICY_PATH.read_text())
    policy['max_corpus_works'] = 1
    with pytest.raises(ValueError, match='пакетный индекс'):
        generate([doc(1, 2015, 'Graph neural networks'), doc(2, 2016, 'Graph neural networks')],
                 date(2017, 1, 1), policy=policy)


def test_unsupported_window_is_rejected():
    with pytest.raises(ValueError, match='quarter'):
        generate([], date(2017, 1, 1), step='month')


def test_yaml_boolean_in_stopwords_is_rejected():
    policy = yaml.safe_load(POLICY_PATH.read_text())
    policy['stopwords'].append(True)
    with pytest.raises(ValueError, match='кавычки YAML'):
        generate([], date(2017, 1, 1), policy=policy)


def test_grammatical_phrases_do_not_become_candidates():
    result = generate([doc(1, 2015, 'Based on machine learning such as neural networks'),
                       doc(2, 2016, 'Based on machine learning such as neural networks')],
                      date(2017, 1, 1))
    phrases = {c['phrase'] for c in result['candidates']}
    assert 'machine learning' in phrases
    assert not phrases & {'based on', 'such as'}


def test_observation_period_filters_before_and_after_without_fake_zero_windows():
    result = generate([doc(1, 2001, 'Ancient phantom phrase'),
                       doc(2, 2013, 'Graph neural networks'),
                       doc(3, 2016, 'Graph neural networks'),
                       doc(4, 2018, 'Later phantom phrase')],
                      date(2020, 1, 1), period_from=date(2010, 1, 1), period_end=date(2017, 1, 1))
    assert result['corpus_work_ids'] == [2, 3]
    assert result['candidates'][0]['publication_series']['points'][-1]['end'] == '2017-01-01'


@pytest.mark.parametrize('selection', ['support', 'rare_recent_title'])
def test_disk_frequencies_match_memory_exactly(selection):
    policy = yaml.safe_load(POLICY_PATH.read_text())
    policy.update(selection=selection, max_document_support=30,
                  recent_birth_windows=3, min_title_support=1)
    docs = [doc(1, 2014, 'Графовые нейронные сети', 'Graph neural networks ' * 4),
            doc(2, 2016, 'Graph neural networks', 'Графовые нейронные сети'),
            doc(3, 2016, 'Graph neural networks extra context'),
            doc(4, 2018, 'Future phantom concept')]
    memory = generate(docs, date(2017, 1, 1), policy=policy)
    policy['frequency_storage'] = 'sqlite'
    disk = generate(list(reversed(docs)), date(2017, 1, 1), policy=policy)
    for key in memory:
        if key not in ('policy_hash', 'effective_policy'):
            assert memory[key] == disk[key], key


def test_disk_policy_processes_more_than_old_limit_without_sampling():
    policy = yaml.safe_load(POLICY_PATH.with_name('terminology.v0.4.1.yaml').read_text())
    old = yaml.safe_load(POLICY_PATH.read_text())
    assert policy['stopwords'] == old['stopwords']
    docs = [doc(i, 2016, 'Graph neural networks') for i in range(20001)]
    result = generate(docs, date(2017, 1, 1), policy=policy)
    assert result['corpus_works'] == len(docs)
    assert result['corpus_work_ids'] == list(range(20001))
    assert all(c['document_support'] == len(docs) for c in result['candidates'])


def test_unknown_frequency_backend_is_rejected():
    policy = yaml.safe_load(POLICY_PATH.read_text())
    policy['frequency_storage'] = 'approximate'
    with pytest.raises(ValueError, match='хранения частот'):
        generate([], date(2017, 1, 1), policy=policy)
