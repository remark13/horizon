import copy

import pytest

from saia.annotation_protocol import (build_packet, compare_submissions,
                                      compare_validated_submissions, load_policy,
                                      validate_submission, verify_validated_submission)


def fixture():
    snapshot = {'snapshot_id': '00000000-0000-0000-0000-000000000001',
                'as_of_date': '2017-01-01', 'period_from': '2010-01-01',
                'period_end_exclusive': '2017-01-01', 'provenance': {},
                'candidates': []}
    candidates, evidence = [], {}
    stages = ['growth_watch', 'declining_activity', 'stable_or_mixed']
    for number in range(1, 9):
        work_id = number
        candidates.append({
            'candidate_id': f'candidate-{number}', 'composition_sha256': f'sha-{number}',
            'label': f'generated label {number}',
            'channels': ['lexical' if number % 2 else 'semantic'],
            'status': 'watch' if number == 1 else 'candidate',
            'work_ids': [work_id],
            'publication_observation': {'stage': stages[number % len(stages)]},
            'publication_series': {'points': [
                {'start': '2016-01-01', 'end': '2017-01-01', 'topic_works': number,
                 'corpus_works': 100, 'share': number / 100, 'complete': True,
                 'coverage_comparable': True}]},
        })
        evidence[work_id] = {'title': f'Paper {number}', 'published_at': '2016-01-01',
                             'sources': [f'https://arxiv.org/abs/1601.{number:05d}']}
    return snapshot, candidates, evidence


def completed(template, reviewer, answer='uncertain'):
    value = copy.deepcopy(template)
    value['reviewer_id'] = reviewer
    value['independent_review_declared'] = True
    value['system_status_not_seen_declared'] = True
    for row in value['annotations']:
        row['answers'] = {name: answer for name in row['answers']}
        row['rationale'] = 'Independent assessment based only on the supplied evidence.'
        row['sources'] = ['https://arxiv.org/abs/1601.00001']
    return value


def test_packet_is_deterministic_blinded_and_excludes_known_controls():
    snapshot, candidates, evidence = fixture()
    first = build_packet(snapshot, candidates, evidence, {1}, max_items=6)
    second = build_packet(snapshot, candidates, evidence, {1}, max_items=6)
    assert first == second
    packet, key, template = first
    assert len(packet['items']) == 6
    assert packet['selection_summary']['excluded_known_control_candidates'] == 1
    assert packet['gold_standard'] is False and packet['calibration_allowed'] is False
    serialized = str(packet['items'])
    assert 'candidate_id' not in serialized and 'system_status' not in serialized
    assert 'watch' not in serialized and 'selection_stratum' not in serialized
    assert all(work['abstract_included'] is False for item in packet['items']
               for work in item['works'])
    assert key['contains_expected_labels'] is False
    assert all(row['reviewer_id'] is None for row in [template])
    assert packet['selection_summary']['selected_by_channel'] == {'lexical': 3, 'semantic': 3}


def test_channel_quota_is_not_distorted_by_more_stages_in_one_channel():
    snapshot, candidates, evidence = fixture()
    for number, candidate in enumerate(candidates):
        if candidate['channels'] == ['lexical']:
            candidate['publication_observation']['stage'] = 'growth_watch'
        else:
            candidate['publication_observation']['stage'] = f'semantic-stage-{number}'
    packet, _, _ = build_packet(snapshot, candidates, evidence, set(), max_items=6)
    assert packet['selection_summary']['selected_by_channel'] == {'lexical': 3, 'semantic': 3}


def test_large_composition_uses_disclosed_temporal_sample():
    snapshot, candidates, evidence = fixture()
    candidate = candidates[0]
    candidate['work_ids'] = list(range(1, 21))
    for number in range(9, 21):
        evidence[number] = {'title': f'Paper {number}',
                            'published_at': f'2016-{(number - 1) % 12 + 1:02d}-01',
                            'sources': [f'https://arxiv.org/abs/1601.{number:05d}']}
    packet, key, _ = build_packet(snapshot, [candidate], evidence, set(), max_items=1)
    item = packet['items'][0]
    assert item['composition_work_count'] == 20 and item['works_shown'] == 12
    assert item['works_truncated'] is True
    assert item['work_sample_algorithm'] == 'evenly-spaced-by-date-v1'
    assert len(item['works']) == 12 and len(key['mapping'][0]['work_ids']) == 20


def test_one_complete_submission_remains_an_opinion_not_calibration():
    snapshot, candidates, evidence = fixture()
    packet, _, template = build_packet(snapshot, candidates, evidence, set(), max_items=4)
    result = validate_submission(packet, completed(template, 'Reviewer A'))
    assert result['complete'] is True
    assert result['individual_opinion_not_gold'] is True
    assert result['calibration_eligible'] is False


def test_partial_submission_is_preserved_but_not_complete():
    snapshot, candidates, evidence = fixture()
    packet, _, template = build_packet(snapshot, candidates, evidence, set(), max_items=4)
    submission = completed(template, 'Reviewer A')
    submission['annotations'].pop()
    result = validate_submission(packet, submission)
    assert result['complete'] is False and len(result['missing_item_ids']) == 1


@pytest.mark.parametrize('change', ['status_seen', 'bad_answer', 'bad_hash', 'duplicate'])
def test_invalid_or_unblinded_submission_is_rejected(change):
    snapshot, candidates, evidence = fixture()
    packet, _, template = build_packet(snapshot, candidates, evidence, set(), max_items=4)
    submission = completed(template, 'Reviewer A')
    if change == 'status_seen':
        submission['system_status_not_seen_declared'] = False
    elif change == 'bad_answer':
        submission['annotations'][0]['answers']['weak_signal_at_cutoff'] = 'forming'
    elif change == 'bad_hash':
        submission['packet_payload_sha256'] = '0' * 64
    else:
        submission['annotations'].append(copy.deepcopy(submission['annotations'][0]))
    with pytest.raises(ValueError):
        validate_submission(packet, submission)


def test_two_distinct_reviews_are_compared_without_automatic_consensus():
    snapshot, candidates, evidence = fixture()
    packet, _, template = build_packet(snapshot, candidates, evidence, set(), max_items=4)
    left = completed(template, 'Reviewer A', 'yes')
    right = completed(template, 'Reviewer B', 'yes')
    right['annotations'][0]['answers']['weak_signal_at_cutoff'] = 'no'
    result = compare_submissions(packet, left, right)
    assert result['consensus_created'] is False and result['calibration_allowed'] is False
    assert result['adjudication_required'] is True
    assert result['axes']['weak_signal_at_cutoff']['raw_agreement'] == .75
    assert len(result['disagreements']) == 1


def test_stored_validated_reviews_can_be_verified_and_compared_but_not_tampered():
    snapshot, candidates, evidence = fixture()
    packet, _, template = build_packet(snapshot, candidates, evidence, set(), max_items=4)
    left = validate_submission(packet, completed(template, 'Reviewer A', 'yes'))
    right = validate_submission(packet, completed(template, 'Reviewer B', 'no'))
    assert verify_validated_submission(packet, left) == left
    result = compare_validated_submissions(packet, left, right)
    assert result['consensus_created'] is False and len(result['disagreements']) == 16
    changed = copy.deepcopy(left)
    changed['annotations'][0]['answers']['weak_signal_at_cutoff'] = 'no'
    with pytest.raises(ValueError, match='повреждена'):
        verify_validated_submission(packet, changed)


def test_same_reviewer_cannot_satisfy_independence():
    snapshot, candidates, evidence = fixture()
    packet, _, template = build_packet(snapshot, candidates, evidence, set(), max_items=4)
    with pytest.raises(ValueError, match='разных'):
        compare_submissions(packet, completed(template, 'Reviewer A'),
                            completed(template, 'reviewer a'))


def test_policy_forbids_automatic_consensus():
    policy = load_policy()
    policy['consensus']['automatic_consensus'] = True
    snapshot, candidates, evidence = fixture()
    with pytest.raises(ValueError, match='консенсус'):
        build_packet(snapshot, candidates, evidence, set(), policy=policy)
