#!/usr/bin/env python3
"""Read-only check of display grouping on an existing card and its saved sources.

No external fetch, collection, re-scoring or expert write. This is an identity
projection check, not a labelled test of weak-signal detection or relevance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8082')
    parser.add_argument('--mission', default='universal-monthly-5177517d-5c5b-4fb8-b987-a66589771915')
    parser.add_argument('--score', type=int, default=9509)
    parser.add_argument('--candidate', type=int, default=7061)
    parser.add_argument('--query', default='Autonomous flight')
    parser.add_argument('--sources', default='nsf_awards,datacite,openaire_projects')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.base != 'http://127.0.0.1:8082' or args.output.exists():
        raise ValueError('Use only the Codex application and a new output directory.')
    def request(path, html=False):
        with urlopen(args.base + path, timeout=30) as response:
            raw = response.read(10_000_001)
            if len(raw) > 10_000_000:
                raise ValueError('Diagnostic response exceeds bound.')
            return raw if html else json.loads(raw)
    before = request(f'/signals/{args.mission}?score_run_id={args.score}')
    selected = urlencode({'score_run_id': args.score, 'query': args.query, 'sources': args.sources})
    prefix = f'/signals/{args.mission}/{args.candidate}'
    context = request(prefix + '/source-context?' + selected)
    initial = {report['observation_id']: request('/external-evidence/observations/' + report['observation_id'])
               for report in context['reports'] if report['observation_id']}
    card = request(prefix + '/brief?' + selected, html=True)
    again = request(prefix + '/source-context?' + selected)
    ending = {identifier: request('/external-evidence/observations/' + identifier) for identifier in initial}
    after = request(f'/signals/{args.mission}?score_run_id={args.score}')
    groups = context['material_grouping']
    flat = [material for report in context['reports'] for material in report['materials']]
    retained = [material for group in groups['groups'] for material in group['records']]
    card_scope = next(c for c in before['cards'] if c['candidate_id'] == args.candidate)
    if context['binding']['composition_sha256'] != card_scope['composition_sha256']:
        raise ValueError('Wrong card composition.')
    if checksum(before) != checksum(after) or checksum(initial) != checksum(ending):
        raise ValueError('Scientific results or saved source observations changed.')
    if checksum(groups) != checksum(again['material_grouping']) or len(flat) != groups['raw_record_count']:
        raise ValueError('Projection is not repeatable or counts changed.')
    if sorted(map(checksum, flat)) != sorted(map(checksum, retained)):
        raise ValueError('Source records are lost from groups.')
    if b'<script' in card.lower() or any(material['url'].encode() not in card for material in retained):
        raise ValueError('Unsafe HTML or a source URL omitted from the readable card.')
    if context['scientific_score_modified'] or groups['independent_confirmation_count'] is not None or groups['funding_amounts_combined']:
        raise ValueError('Grouping must not assert science, independence or financial totals.')
    report = {'health': request('/health'), 'scope': 'read-only external identity grouping, not accuracy or signal validation',
        'mission_id': args.mission, 'score_run_id': args.score, 'candidate_id': args.candidate, 'query': args.query,
        'source_observation_ids': sorted(initial), 'source_payload_checksums': {identifier: checksum(initial[identifier]['payload']) for identifier in initial},
        'scientific_cards_sha256_before': checksum(before), 'scientific_cards_sha256_after': checksum(after),
        'scientific_cards_unchanged': True, 'source_observations_unchanged': True, 'all_records_retained': True,
        'external_fetch_requested': False, 'scientific_jobs_started': False, 'expert_writes_performed': False,
        'raw_record_count': groups['raw_record_count'], 'display_group_count': groups['display_group_count'],
        'collapsed_record_count': groups['collapsed_record_count'], 'merged_group_count': groups['merged_group_count'],
        'conflict_count': len(groups['conflicts']), 'grouping_sha256': checksum(groups), 'projection_repeatable': True,
        'brief_bytes': len(card), 'brief_sha256': hashlib.sha256(card).hexdigest(), 'accuracy_improvement_measured': False,
        'groups': [{'group_id': g['group_id'], 'primary_title': g['primary']['title_original'],
                    'record_count': g['record_count'], 'source_count': g['source_count'],
                    'urls': [m['url'] for m in g['records']], 'merge_evidence': g['merge_evidence']} for g in groups['groups']]}
    args.output.mkdir(parents=True)
    (args.output / 'checkpoint.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'context.json').write_text(json.dumps(context, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / f'card-{args.candidate}.html').write_bytes(card)
    print(json.dumps({key: val for key, val in report.items() if key != 'groups'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
