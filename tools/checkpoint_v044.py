"""Freeze a read-only replay of the new trajectory descriptors against v0.4.3."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from saia.assessment_store import validate_report
from saia.hybrid import digest
from saia.publication_observation import observe


def read(path):
    return json.loads(path.read_text()), hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--assessment', type=Path, required=True)
    parser.add_argument('--previous-screen', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; select a new versioned path.')
    snapshot, snapshot_bytes = read(args.snapshot)
    assessment, assessment_bytes = read(args.assessment)
    previous, previous_bytes = read(args.previous_screen)
    validate_report(snapshot, assessment)
    if digest({k: v for k, v in previous.items() if k != 'report_sha256'}) != previous['report_sha256']:
        raise ValueError('Previous screen payload checksum does not match.')
    if previous['snapshot_sha256'] != digest(snapshot):
        raise ValueError('Previous screen belongs to another input.')
    policy = previous['publication_observations']['effective_policy']
    if digest(policy) != previous['publication_observations']['policy_hash']:
        raise ValueError('Previous screening policy checksum does not match.')
    measured = {c['candidate_id']: c['observed'] for c in assessment['candidates']}
    prior = {c['candidate_id']: c for c in previous['candidates']}
    if set(prior) != {c['candidate_id'] for c in snapshot['candidates']}:
        raise ValueError('Previous candidate inventory is different.')
    started = time.perf_counter()
    rows = []
    for c in snapshot['candidates']:
        obs = observe(c, policy, measured[c['candidate_id']])
        old = prior[c['candidate_id']]['observation']
        for field in ('stage', 'observed', 'diffusion', 'coverage_verified', 'validation', 'map_position'):
            if obs[field] != old[field]:
                raise ValueError('Historical screen decision or metric changed: ' + field)
        rows.append({'candidate_id': c['candidate_id'], 'label': c['label'],
                     'publication_observation': obs})
    stages = dict(Counter(r['publication_observation']['stage'] for r in rows))
    if stages != previous['publication_observations']['stages']:
        raise ValueError('Historical counts changed.')
    root = Path(__file__).resolve().parents[1]
    result = {
        'version': 'publication-trajectory-checkpoint-0.4.4',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'snapshot_id': snapshot['snapshot_id'], 'snapshot_payload_sha256': digest(snapshot),
        'assessment_payload_sha256': assessment['assessment_content_sha256'],
        'file_bytes_sha256': {'snapshot': snapshot_bytes, 'assessment': assessment_bytes,
                              'previous_screen': previous_bytes},
        'screening_policy': policy, 'screening_policy_sha256': digest(policy),
        'source_code_bytes_sha256': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                     for name in ['saia/api.py', 'saia/measurement.py', 'saia/methodology.py',
                                                  'saia/publication_observation.py', 'saia/candidate_evidence_audit.py',
                                                  'tools/checkpoint_v044.py']},
        'strict_counts_unchanged': assessment['counts'] == previous['strict_counts']['assessment_statuses'],
        'screen_decisions_and_core_metrics_unchanged': True, 'stages': stages,
        'candidates_with_recent_decline_despite_positive_long_window': [
            r['candidate_id'] for r in rows if
            r['publication_observation']['stage'] == 'publication_signal_candidate' and (
                r['publication_observation']['recent_trajectory']['positive_long_window_but_recent_count_decline'] or
                r['publication_observation']['recent_trajectory']['positive_long_window_but_recent_share_decline'])],
        'measurement_only_seconds': time.perf_counter() - started,
        'host_free_bytes_at_measurement': shutil.disk_usage(root).free,
        'accuracy_evaluated': False, 'modern_corpus_processed': False,
        'candidates': rows,
    }
    if not result['strict_counts_unchanged']:
        raise ValueError('Historical strict counts changed.')
    result['report_payload_sha256'] = digest(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('version', 'stages', 'strict_counts_unchanged',
          'candidates_with_recent_decline_despite_positive_long_window', 'measurement_only_seconds',
          'report_payload_sha256')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
