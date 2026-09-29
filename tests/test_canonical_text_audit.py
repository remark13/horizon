from copy import deepcopy

import pytest

from saia import canonical_text
from scripts.audit_canonical_text import analyze_payload, content_sha256, verify_payload, VERSION


def frozen():
    observed = '2026-09-15T00:00:00+00:00'
    sources = []
    snapshots = []
    for raw_id, source, text in [(1, 'openalex', 'Wrong aggregate abstract'),
                                  (2, 'arxiv', 'Original native abstract')]:
        sources.append({'raw_record_id': raw_id, 'source': source,
                        'source_record_id': 'W1' if source == 'openalex' else '1601.00001',
                        'title_key': 'method', 'abstract': text,
                        'abstract_sha256': canonical_text.text_sha256(text),
                        'arxiv_ids': ['1601.00001'], 'snapshot_id': raw_id, 'observed_at': observed,
                        'metadata': {} if source == 'openalex' else
                        {'id': '1601.00001', 'created': '2016-01-01', 'updated': '2016-01-01',
                         '_source_format': 'hf-arxiv-parquet-snapshot', '_submission_date_raw': '1 Jan 2016'}})
        snapshots.append({'snapshot_id': raw_id, 'source': source, 'fetched_at': observed})
    body = {'version': VERSION, 'mission_id': 'example', 'normalize_run_id': 1,
            'work_version_count': 2, 'candidate_count': 2, 'source_snapshots': snapshots,
            'works': [{'work_id': 1, 'canonical_title': 'Method', 'canonical_title_key': 'method',
                       'old_abstract': sources[0]['abstract'], 'old_abstract_sha256': sources[0]['abstract_sha256'],
                       'candidates': sources}]}
    payload = {**body, 'content_sha256': content_sha256(body)}
    cfg = {'mission_id': 'example', 'normalize_run_id': 1, 'expected_works': 1, 'expected_work_versions': 2,
           'max_works': 10, 'max_candidates': 10, 'historical_cutoff_exclusive': '2017-01-01',
           'current_text_cutoff_exclusive': '2026-09-29', 'detail_example_work_ids': [1]}
    return payload, cfg


def test_audit_separates_current_source_choice_from_historical_verification():
    payload, cfg = frozen()
    original = deepcopy(payload)
    result = analyze_payload(payload, cfg)
    assert result['counts']['historical']['abstracts_changed_from_frozen_work'] == 0
    assert result['counts']['current_text']['abstracts_changed_from_frozen_work'] == 1
    example = result['debug_examples_not_accuracy_labels'][0]
    assert example['variants']['historical']['chosen_source'] == 'openalex'
    assert example['variants']['current_text']['chosen_source'] == 'arxiv'
    assert result['new_signal_detection_or_precision_measured'] is False
    assert payload == original


def test_audit_rejects_hash_changes_and_source_text_changes_even_if_wrapper_is_rehashed():
    payload, cfg = frozen()
    payload['works'][0]['candidates'][0]['abstract'] = 'Modified source text'
    with pytest.raises(ValueError, match='checksum'):
        verify_payload(payload, cfg)
    payload['content_sha256'] = content_sha256({k: v for k, v in payload.items() if k != 'content_sha256'})
    with pytest.raises(ValueError, match='Source text'):
        verify_payload(payload, cfg)


def test_debug_example_ids_do_not_affect_source_choices_or_counts():
    payload, cfg = frozen()
    result = analyze_payload(payload, cfg)
    cfg['detail_example_work_ids'] = []
    other = analyze_payload(payload, cfg)
    assert other['counts'] == result['counts']
    assert other['debug_examples_not_accuracy_labels'] == []
