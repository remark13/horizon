from copy import deepcopy
from datetime import date

import numpy as np
import pytest

from saia import composition_assessment as ca, hybrid
from saia.terminology import PublicationText


def fixture(comparable=False):
    docs = [PublicationText(i + 1, date(2010 + i, 2, 1), f'Original result {i}', 'Research text') for i in range(7)]
    semantic = [{'topic_id': 1, 'label': 'line one', 'work_ids': [1, 4, 6, 7]},
                {'topic_id': 2, 'label': 'line two', 'work_ids': [3, 5, 6]}]
    cs = hybrid.fuse([], semantic, {d.work_id for d in docs})
    hybrid.add_series(cs, docs, date(2017, 1, 1), date(2010, 1, 1), date(2017, 1, 1), 'year', comparable)
    snapshot = {'snapshot_id': 'fixture', 'as_of_date': '2017-01-01', 'period_from': '2010-01-01',
                'period_end_exclusive': '2017-01-01', 'window_step': 'year',
                'eligible_work_ids': list(range(1, 8)), 'input_text_hash': ca.text_hash(docs),
                'provenance': {'embedding_model': 'fixture-model'}, 'candidates': cs}
    evidence = {d.work_id: {'vector': [1., float(d.work_id) / 10], 'source_quality': .9,
                            'authors': [], 'organisations': []} for d in docs}
    return snapshot, docs, evidence


@pytest.mark.parametrize('n', [2, 5, 100])
def test_linear_memory_coherence_matches_exact_quadratic_formula(n):
    matrix = np.random.default_rng(37).normal(size=(n, 7))
    unit = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)
    expected = (unit @ unit.T)[np.triu_indices(n, 1)].mean()
    assert ca.exact_coherence(matrix.tolist()) == pytest.approx(expected, abs=1e-12)


def test_coherence_covers_zero_negative_identical_and_missing():
    assert ca.exact_coherence([[1, 0], [0, 1]]) == pytest.approx(0.)
    assert ca.exact_coherence([[1, 0], [-1, 0]]) == pytest.approx(-1.)
    assert ca.exact_coherence([[1, 0], [3, 0]]) == pytest.approx(1.)
    assert ca.exact_coherence([]) is None
    assert ca.exact_coherence([[1, 0]]) is None


@pytest.mark.parametrize('vectors', [[[0, 0]], [[float('nan'), 1]], [[float('inf'), 1]], [[1], [1, 2]], [[]]])
def test_invalid_vectors_are_not_silent_coherence(vectors):
    with pytest.raises(ValueError): ca.exact_coherence(vectors)


def test_unknowns_preserved_and_original_snapshot_not_mutated():
    snapshot, docs, evidence = fixture()
    original = deepcopy(snapshot)
    result = ca.assess(snapshot, docs, evidence)
    assert snapshot == original
    assert result['snapshot_content_sha256'] == hybrid.digest(original)
    assert len(result['candidates']) == 2
    for row in result['candidates']:
        assert row['assessment']['status'] != 'forming'
        assert row['assessment']['emergence_score'] is None
        assert {'G6_primary_sources', 'G3_independent_teams', 'G_coverage', 'G_coherence_calibration'} <= set(row['blocking_unknowns'])
        assert row['observed']['primary_sources'] is None
        assert row['observed']['teams'] is None
        assert row['normalized']['independent_diffusion'] is None
        assert row['publication_series']['share_slope_per_window'] is None
        assert row['observed']['embedding_coverage'] == 1.
    sha = result.pop('assessment_content_sha256')
    assert hybrid.digest(result) == sha


def test_missing_vector_does_not_measure_complete_composition():
    snapshot, docs, evidence = fixture(True)
    evidence[6]['vector'] = None
    result = ca.assess(snapshot, docs, evidence)
    for row in result['candidates']:
        assert row['observed']['coherence'] is None
        assert row['observed']['available_vector_coherence'] is not None
        assert row['observed']['vector_works'] == len(row['work_ids']) - 1
        assert row['observed']['embedding_coverage'] == 0.


def test_author_conflicts_reduce_identity_coverage_and_are_hashed():
    snapshot, docs, evidence = fixture(True)
    for i, row in evidence.items():
        row['authors'] = [f'A{i}']
        row['organisations'] = [i]
    before = ca.assess(snapshot, docs, evidence)
    for row in evidence.values():
        row['author_identity_conflict'] = True
    after = ca.assess(snapshot, docs, evidence)
    assert before['input_evidence_sha256'] != after['input_evidence_sha256']
    for row in after['candidates']:
        assert row['observed']['teams'] is None
        assert row['observed']['identity_coverage'] == 0.
        assert row['observed']['author_identity_conflict_works'] == len(row['work_ids'])


def test_external_evidence_uses_only_openalex_include_rows():
    evidence = {
        1: {'authors': [], 'organisations': [], 'author_identity_conflict': False},
        2: {'authors': [], 'organisations': [], 'author_identity_conflict': False},
        3: {'authors': [], 'organisations': [], 'author_identity_conflict': False},
    }
    source = {
        11: {'has_openalex_version': True, 'quality_decision': 'include',
             'authors': ['A2', 'A1', 'A1'], 'organisations': ['ror:X'],
             'author_identity_conflict': True},
        12: {'has_openalex_version': True, 'quality_decision': 'quarantine',
             'authors': ['A3'], 'organisations': ['ror:Y']},
        13: {'has_openalex_version': False, 'quality_decision': 'include',
             'authors': ['A4'], 'organisations': ['ror:Z']},
    }
    counts = ca.apply_external_evidence(evidence, {1: 11, 2: 12, 3: 13}, source)
    assert evidence[1]['authors'] == ['A1', 'A2']
    assert evidence[1]['organisations'] == ['ror:X']
    assert evidence[1]['author_identity_conflict'] is True
    assert evidence[2]['authors'] == [] and evidence[3]['authors'] == []
    assert counts == {
        'target_works': 3, 'exact_arxiv_matches': 3,
        'matched_with_openalex_version': 2, 'adopted': 1,
        'with_author_ids': 1, 'with_organisations': 1,
        'with_identity_conflict': 1, 'source_quality_not_include': 1,
        'without_openalex_version': 1,
    }


def test_external_evidence_provenance_is_hashed_and_does_not_change_composition():
    snapshot, docs, evidence = fixture(True)
    provenance = {'source_normalize_run_id': 7, 'counts': {'adopted': 2}}
    result = ca.assess(snapshot, docs, evidence,
                       external_evidence_provenance=provenance)
    assert result['external_evidence_provenance'] == provenance
    assert [row['work_ids'] for row in result['candidates']] == [
        candidate['work_ids'] for candidate in snapshot['candidates']
    ]
    body = {key: value for key, value in result.items()
            if key != 'assessment_content_sha256'}
    assert hybrid.digest(body) == result['assessment_content_sha256']


def test_frozen_legacy_policy_does_not_change_with_new_identity_flag():
    import yaml
    policy = yaml.safe_load(ca.POLICY_PATH.read_text())
    policy.pop('reject_author_identity_conflicts')
    policy['version'] = 'composition-assessment-0.4.1-experimental'
    snapshot, docs, evidence = fixture(True)
    before = ca.assess(snapshot, docs, evidence, policy=policy)
    for row in evidence.values():
        row['author_identity_conflict'] = True
    after = ca.assess(snapshot, docs, evidence, policy=policy)
    assert before == after
    assert all('author_identity_conflict_works' not in r['observed'] for r in after['candidates'])


def test_first_window_novelty_does_not_use_later_member_vectors():
    snapshot, docs, evidence = fixture(True)
    before = ca.assess(snapshot, docs, evidence)
    changed = deepcopy(evidence)
    changed[4]['vector'] = [-1., 0.]
    after = ca.assess(snapshot, docs, changed)
    assert before['input_evidence_sha256'] != after['input_evidence_sha256']
    for first, second in zip(before['candidates'], after['candidates']):
        assert first['observed']['novelty_raw'] == second['observed']['novelty_raw']
    assert any(r['observed']['novelty_raw'] is None for r in before['candidates'])


def test_lexical_compositions_do_not_change_semantic_percentiles():
    snapshot, docs, evidence = fixture(True)
    before = ca.assess(snapshot, docs, evidence)
    lexical = hybrid.fuse([{'phrase': 'literal phrase', 'work_ids': [5, 7], 'contexts': []}], [], set(range(1, 8)))
    hybrid.add_series(lexical, docs, date(2017, 1, 1), date(2010, 1, 1), date(2017, 1, 1), 'year', True)
    snapshot['candidates'].extend(lexical)
    after = ca.assess(snapshot, docs, evidence)
    assert {r['candidate_id']: r for r in before['candidates']} == {r['candidate_id']: r for r in after['candidates'] if r['channels'] == ['semantic']}


@pytest.mark.parametrize('change', ['text', 'work', 'date', 'count', 'denominator', 'candidate_id', 'duplicate', 'dimension', 'quality'])
def test_input_mismatch_rejected(change):
    snapshot, docs, evidence = fixture()
    if change == 'text': docs[0] = PublicationText(1, docs[0].published_at, 'Changed', 'Research text')
    if change == 'work': evidence.pop(1)
    if change == 'date': snapshot['period_end_exclusive'] = '2015-01-01'
    if change == 'count': snapshot['candidates'][0]['publication_series']['points'][0]['topic_works'] += 1
    if change == 'denominator': snapshot['candidates'][0]['publication_series']['points'][0]['corpus_works'] += 1
    if change == 'candidate_id': snapshot['candidates'][0]['candidate_id'] = 'foreign'
    if change == 'duplicate': snapshot['candidates'].append(snapshot['candidates'][0])
    if change == 'dimension': evidence[7]['vector'] = [1.]
    if change == 'quality': evidence[7]['source_quality'] = 2.
    with pytest.raises(ValueError): ca.assess(snapshot, docs, evidence)


def test_repeated_assessment_is_deterministic():
    args = fixture(True)
    assert ca.assess(*args) == ca.assess(*args)


def test_export_does_not_overwrite_or_run_analysis(tmp_path, monkeypatch):
    file = tmp_path / 'previous.json'
    file.write_text('preserved')
    monkeypatch.setattr('sys.argv', ['assessment', 'fixture', '--export', str(file)])
    monkeypatch.setattr(ca, 'analyze', lambda _: pytest.fail('must not run'))
    with pytest.raises(SystemExit) as error: ca.main()
    assert error.value.code == 2 and file.read_text() == 'preserved'


def test_replay_uses_saved_passports_not_current_defaults(monkeypatch):
    from saia import methodology
    args = fixture(True)
    previous = ca.assess(*args)
    monkeypatch.setattr(methodology, 'load_default', lambda: pytest.fail('no current defaults'))
    monkeypatch.setattr(ca.runs, 'code_version', lambda: 'different-code-fingerprint')
    calls = []
    def recompute(identifier, config, policy, measurement,
                  external_run=None, external_quality=None):
        calls.append(identifier)
        assert external_run is None and external_quality is None
        assert policy == previous['effective_policy']
        assert measurement == previous['effective_measurement_policy']
        return ca.assess(*args, config, policy, measurement)
    monkeypatch.setattr(ca, 'analyze', recompute)
    result = ca.replay(previous)
    assert calls == ['fixture'] and result['replay_scientific_content_equal'] is True
    assert result['replay_code_version_equal'] is False
    assert previous['assessment_content_sha256'] == result['replay_from_assessment_sha256']
    assert previous['candidates'] == result['candidates']
    assert 'replay_from_assessment_sha256' not in previous


def test_replay_reuses_explicit_external_evidence_generation(monkeypatch):
    args = fixture(True)
    external = {
        'source_normalize_run_id': 2517,
        'source_quality_generation_id': 761,
        'counts': {'adopted': 3},
    }
    previous = ca.assess(*args, external_evidence_provenance=external)
    calls = []

    def recompute(identifier, config, policy, measurement,
                  external_run=None, external_quality=None):
        calls.append((external_run, external_quality))
        return ca.assess(*args, config, policy, measurement,
                         external_evidence_provenance=external)

    monkeypatch.setattr(ca, 'analyze', recompute)
    replayed = ca.replay(previous)
    assert calls == [(2517, 761)]
    assert replayed['replay_scientific_content_equal'] is True


def test_replay_rejects_changed_inputs_and_preserves_previous(monkeypatch):
    previous = ca.assess(*fixture(True))
    original = deepcopy(previous)
    changed = deepcopy(previous)
    changed['input_evidence_sha256'] = 'changed'
    monkeypatch.setattr(ca, 'analyze', lambda *args: changed)
    with pytest.raises(ValueError, match='Повтор не совпал'): ca.replay(previous)
    assert previous == original


@pytest.mark.parametrize('change', ['content', 'passport'])
def test_replay_checks_report_and_embedded_passport_hashes(monkeypatch, change):
    previous = ca.assess(*fixture(True))
    previous['effective_policy']['min_last_full_window_share_for_widespread'] = .2
    if change == 'passport':
        previous.pop('assessment_content_sha256')
        previous['assessment_content_sha256'] = hybrid.digest(previous)
    monkeypatch.setattr(ca, 'analyze', lambda *args: pytest.fail('invalid report must not run'))
    with pytest.raises(ValueError, match='Отпечаток|Хеш'): ca.replay(previous)


def test_embedded_methodology_is_validated_and_copied():
    from saia import methodology
    original = methodology.load_default()
    restored = methodology.from_mapping(original.raw)
    assert restored.config_hash == original.config_hash
    restored.raw['version'] = 'changed'
    assert original.version == original.raw['version']
    invalid = deepcopy(original.raw)
    invalid['scoring']['configurations']['publication_v4']['weights']['novelty'] = .99
    with pytest.raises(methodology.MethodologyError): methodology.from_mapping(invalid)
