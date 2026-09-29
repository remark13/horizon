"""Blinded, immutable expert-review packets without manufactured gold labels."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from saia import db, hybrid
from saia.evaluation_catalog import load as load_catalog
from saia.portfolio import project

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config' / 'independent-annotation.v0.4.21.yaml'
CURRENT_PACKET_PATH = (Path(__file__).resolve().parents[1] / 'evaluation'
                       / 'ml-area-2017-independent-review-v0421-r4.packet.json')
CURRENT_TEMPLATE_PATH = (Path(__file__).resolve().parents[1] / 'evaluation'
                         / 'ml-area-2017-independent-review-v0421-r4.template.json')
VERSION = 'independent-annotation-packet-0.4.21'
SUBMISSION_VERSION = 'independent-annotation-submission-0.4.21'
COMPARISON_VERSION = 'independent-annotation-comparison-0.4.21'


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = yaml.safe_load(path.read_text())
    validate_policy(policy)
    return policy


def validate_policy(policy: dict) -> None:
    selection = policy.get('selection') or {}
    if selection.get('algorithm') != 'equal-channel-quota-then-stage-round-robin-v2':
        raise ValueError('Неизвестная версия алгоритма выборки экспертного пакета.')
    if (not 2 <= selection.get('visible_works_per_item', 0) <= 50
            or selection.get('work_sample_algorithm') != 'evenly-spaced-by-date-v1'):
        raise ValueError('Некорректная политика видимой выборки публикаций.')
    if not 1 <= selection.get('default_items', 0) <= selection.get('hard_max_items', 0) <= 100:
        raise ValueError('Некорректные границы размера экспертного пакета.')
    if selection.get('balance_by') != ['channel', 'publication_observation_stage']:
        raise ValueError('Пакет должен балансироваться по каналу и наблюдаемой стадии.')
    required = {'research_line_coherence', 'primary_result_evidence',
                'independent_groups_evidence', 'weak_signal_at_cutoff'}
    if set(policy.get('questions') or {}) != required:
        raise ValueError('Набор вопросов экспертной разметки изменён без новой версии.')
    for question in policy['questions'].values():
        choices = question.get('choices') or []
        if len(choices) != len(set(choices)) or not choices:
            raise ValueError('У каждого вопроса нужен непустой набор разных ответов.')
    hidden = set((policy.get('blinding') or {}).get('hidden_per_item') or [])
    if not {'candidate_id', 'system_status', 'gates', 'selection_stratum'} <= hidden:
        raise ValueError('Ослеплённый пакет не должен раскрывать решение системы.')
    if (policy.get('consensus') or {}).get('automatic_consensus') is not False:
        raise ValueError('Автоматический экспертный консенсус запрещён.')


def _stable_rank(seed: str, candidate_id: str) -> str:
    return hashlib.sha256(f'{seed}\0{candidate_id}'.encode()).hexdigest()


def _stratum(candidate: dict) -> tuple[str, str]:
    channels = candidate.get('channels') or []
    channel = '+'.join(sorted(channels)) or 'unknown'
    stage = (candidate.get('publication_observation') or {}).get('stage') or 'unknown'
    return channel, stage


def _series(candidate: dict) -> list[dict]:
    points = (candidate.get('publication_series') or {}).get('points') or []
    return [{key: point.get(key) for key in
             ('start', 'end', 'topic_works', 'corpus_works', 'share', 'complete',
              'coverage_comparable')} for point in points]


def _visible_work_ids(work_ids: list[int], work_evidence: dict[int, dict], limit: int) -> list[int]:
    ordered = sorted(work_ids, key=lambda i: (
        work_evidence[i]['published_at'], work_evidence[i]['title'], i))
    if len(ordered) <= limit:
        return ordered
    indexes = [round(index * (len(ordered) - 1) / (limit - 1)) for index in range(limit)]
    if len(indexes) != len(set(indexes)):
        raise ValueError('Временная выборка публикаций дала повторные позиции.')
    return [ordered[index] for index in indexes]


def build_packet(snapshot: dict, candidates: list[dict], work_evidence: dict[int, dict],
                 excluded_work_ids: set[int], policy: dict | None = None,
                 max_items: int | None = None, seed: str | None = None) -> tuple[dict, dict, dict]:
    """Build reviewer packet, private mapping key and empty submission template."""
    policy = copy.deepcopy(policy or load_policy())
    validate_policy(policy)
    selection = policy['selection']
    limit = max_items if max_items is not None else selection['default_items']
    if not 1 <= limit <= selection['hard_max_items']:
        raise ValueError('Размер пакета выходит за границы политики.')
    seed = seed or selection['seed']
    eligible, excluded_controls = [], 0
    for candidate in candidates:
        work_ids = list(candidate.get('work_ids') or [])
        if not work_ids or any(work_id not in work_evidence for work_id in work_ids):
            raise ValueError('Для каждого кандидата нужны метаданные всех работ.')
        if set(work_ids) & excluded_work_ids:
            excluded_controls += 1
            continue
        eligible.append(candidate)
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for candidate in eligible:
        buckets[_stratum(candidate)].append(candidate)
    for rows in buckets.values():
        rows.sort(key=lambda row: _stable_rank(seed, row['candidate_id']))
    chosen = []
    channels = sorted({channel for channel, _ in buckets})
    # Equal channel quotas first; stages are round-robined within each channel.
    # Otherwise a channel with more observable stage values dominates the sample.
    for channel_index, channel in enumerate(channels):
        quota = limit // len(channels) + (channel_index < limit % len(channels))
        stages = sorted(stage for current, stage in buckets if current == channel)
        while quota:
            changed = False
            for stage in stages:
                if buckets[(channel, stage)] and quota:
                    chosen.append(buckets[(channel, stage)].pop(0))
                    quota -= 1
                    changed = True
            if not changed:
                break
    # If one channel was too small, deterministically fill the unused quota.
    while len(chosen) < limit:
        changed = False
        for stratum in sorted(buckets):
            if buckets[stratum] and len(chosen) < limit:
                chosen.append(buckets[stratum].pop(0))
                changed = True
        if not changed:
            break
    if not chosen:
        raise ValueError('После исключения контрольных примеров пакет пуст.')

    scope = {'version': VERSION, 'snapshot_id': snapshot['snapshot_id'],
             'snapshot_content_sha256': hybrid.digest(snapshot),
             'policy_sha256': hybrid.digest(policy), 'seed': seed,
             'max_items': limit, 'candidate_ids': [c['candidate_id'] for c in chosen]}
    package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, hybrid.digest(scope)))
    items, mapping = [], []
    for candidate in chosen:
        composition = candidate.get('composition_sha256') or hybrid.digest(sorted(candidate['work_ids']))
        item_id = 'item-' + hashlib.sha256(
            f'{package_id}\0{composition}'.encode()).hexdigest()[:20]
        visible_ids = _visible_work_ids(candidate['work_ids'], work_evidence,
                                       selection['visible_works_per_item'])
        works = []
        for work_id in visible_ids:
            evidence = work_evidence[work_id]
            works.append({'title': evidence['title'], 'published_at': evidence['published_at'],
                          'sources': list(evidence.get('sources') or []),
                          'abstract_included': False,
                          'abstract_omission_reason': policy['blinding']['abstract_reason']})
        items.append({
            'item_id': item_id,
            'generated_label': candidate['label'],
            'as_of_date': snapshot['as_of_date'],
            'period_from': snapshot.get('period_from'),
            'period_end_exclusive': snapshot.get('period_end_exclusive'),
            'composition_work_count': len(candidate['work_ids']),
            'works_shown': len(works),
            'works_truncated': len(works) < len(candidate['work_ids']),
            'work_sample_algorithm': selection['work_sample_algorithm'],
            'publication_series': _series(candidate),
            'works': works,
            'questions': {name: {'choices': spec['choices'], 'meaning': spec['meaning']}
                          for name, spec in policy['questions'].items()},
            'evidence_boundary': ('Только библиографические метаданные и динамика в закреплённом '
                                  'корпусе; это не сведения о рынке или будущем успехе.'),
        })
        mapping.append({
            'item_id': item_id,
            'candidate_id': candidate['candidate_id'],
            'composition_sha256': composition,
            'selection_stratum': list(_stratum(candidate)),
            'hidden_system_status': candidate.get('status'),
            'hidden_publication_stage': (candidate.get('publication_observation') or {}).get('stage'),
            'work_ids': sorted(candidate['work_ids']),
        })
    counts = Counter(row['selection_stratum'][0] for row in mapping)
    packet = {
        'version': VERSION,
        'package_id': package_id,
        'purpose': policy['purpose'],
        'snapshot_id': snapshot['snapshot_id'],
        'snapshot_content_sha256': scope['snapshot_content_sha256'],
        'policy_version': policy['version'],
        'policy_sha256': scope['policy_sha256'],
        'blinding': {'system_status_hidden': True, 'gates_hidden': True,
                     'selection_stratum_hidden_per_item': True,
                     'expected_labels_present': False, 'market_success_question_present': False},
        'selection_summary': {'eligible_candidates': len(eligible),
                              'algorithm': selection['algorithm'],
                              'excluded_known_control_candidates': excluded_controls,
                              'selected_items': len(items),
                              'selected_by_channel': dict(sorted(counts.items()))},
        'reviewer_instructions': [
            'Оценивайте только данные на указанную дату среза.',
            'Не считайте рост публикаций доказательством рынка или будущего успеха.',
            'Если данных недостаточно, выбирайте uncertain или not_assessable.',
            'Не открывайте private key до отправки индивидуального решения.',
        ],
        'items': items,
        'calibration_allowed': False,
        'gold_standard': False,
    }
    packet['packet_payload_sha256'] = hybrid.digest(packet)
    key = {'version': VERSION + '-private-key', 'package_id': package_id,
           'packet_payload_sha256': packet['packet_payload_sha256'],
           'contains_expected_labels': False, 'mapping': mapping}
    key['key_payload_sha256'] = hybrid.digest(key)
    template = {
        'version': SUBMISSION_VERSION,
        'package_id': package_id,
        'packet_payload_sha256': packet['packet_payload_sha256'],
        'reviewer_id': None,
        'independent_review_declared': None,
        'system_status_not_seen_declared': None,
        'annotations': [
            {'item_id': item['item_id'],
             'answers': {name: None for name in policy['questions']},
             'rationale': None, 'sources': []}
            for item in items
        ],
    }
    return packet, key, template


def _verified_packet(packet: dict) -> dict:
    packet = copy.deepcopy(packet)
    expected = packet.pop('packet_payload_sha256', None)
    if packet.get('version') != VERSION or not expected or hybrid.digest(packet) != expected:
        raise ValueError('Хеш или версия экспертного пакета не совпали.')
    packet['packet_payload_sha256'] = expected
    return packet


def current_packet(path: Path = CURRENT_PACKET_PATH) -> dict:
    """Read and verify the accepted public packet; never exposes the private key."""
    return _verified_packet(json.loads(path.read_text()))


def current_template(path: Path = CURRENT_TEMPLATE_PATH) -> dict:
    packet = current_packet()
    template = json.loads(path.read_text())
    if (template.get('version') != SUBMISSION_VERSION
            or template.get('package_id') != packet['package_id']
            or template.get('packet_payload_sha256') != packet['packet_payload_sha256']
            or {row.get('item_id') for row in template.get('annotations') or []}
            != {item['item_id'] for item in packet['items']}):
        raise ValueError('Шаблон анкеты не совпал с принятым экспертным пакетом.')
    return template


def _clean_sources(sources: list[str], maximum: int) -> list[str]:
    if not isinstance(sources, list) or len(sources) > maximum:
        raise ValueError('Некорректное число экспертных источников.')
    clean = []
    for value in sources:
        if not isinstance(value, str):
            raise ValueError('Экспертный источник должен быть HTTPS-ссылкой.')
        link = value.strip()
        parsed = urlsplit(link)
        if (len(link) > 2048 or parsed.scheme != 'https' or not parsed.hostname
                or parsed.username or parsed.password or any(ch.isspace() for ch in link)):
            raise ValueError('Экспертный источник должен быть HTTPS-ссылкой без credentials.')
        if link not in clean:
            clean.append(link)
    return clean


def validate_submission(packet: dict, submission: dict, policy: dict | None = None) -> dict:
    packet = _verified_packet(packet)
    policy = copy.deepcopy(policy or load_policy())
    validate_policy(policy)
    if (submission.get('version') != SUBMISSION_VERSION
            or submission.get('package_id') != packet['package_id']
            or submission.get('packet_payload_sha256') != packet['packet_payload_sha256']):
        raise ValueError('Анкета относится к другому экспертному пакету.')
    reviewer = submission.get('reviewer_id')
    if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 120:
        raise ValueError('Нужен идентификатор рецензента.')
    if (policy['submission']['require_independent_review_declaration']
            and submission.get('independent_review_declared') is not True):
        raise ValueError('Нужно явно подтвердить независимое заполнение анкеты.')
    if (policy['submission']['require_system_status_not_seen_declaration']
            and submission.get('system_status_not_seen_declared') is not True):
        raise ValueError('Нужно подтвердить, что статус системы не был показан.')
    expected_items = {item['item_id'] for item in packet['items']}
    seen, rows = set(), []
    for annotation in submission.get('annotations') or []:
        item_id = annotation.get('item_id')
        if item_id not in expected_items or item_id in seen:
            raise ValueError('Неизвестный или повторный item_id в анкете.')
        seen.add(item_id)
        answers = annotation.get('answers') or {}
        if set(answers) != set(policy['questions']):
            raise ValueError('Ответы должны покрывать точный набор вопросов.')
        for name, answer in answers.items():
            if answer not in policy['questions'][name]['choices']:
                raise ValueError('Недопустимый ответ в экспертной анкете.')
        rationale = annotation.get('rationale')
        if not isinstance(rationale, str):
            raise ValueError('К каждому решению нужно объяснение.')
        rationale = rationale.strip()
        limits = policy['submission']
        if not limits['rationale_min_length'] <= len(rationale) <= limits['rationale_max_length']:
            raise ValueError('Объяснение выходит за границы политики.')
        rows.append({'item_id': item_id, 'answers': answers, 'rationale': rationale,
                     'sources': _clean_sources(annotation.get('sources') or [], limits['max_sources'])})
    missing = sorted(expected_items - seen)
    result = {
        'version': SUBMISSION_VERSION + '-validated',
        'package_id': packet['package_id'],
        'packet_payload_sha256': packet['packet_payload_sha256'],
        'reviewer_id': reviewer.strip(),
        'identity': 'self_declared_not_authenticated',
        'independent_review_declared': True,
        'system_status_not_seen_declared': True,
        'complete': not missing,
        'missing_item_ids': missing,
        'annotations': sorted(rows, key=lambda row: row['item_id']),
        'individual_opinion_not_gold': True,
        'calibration_eligible': False,
        'interpretation': ('Даже полная анкета одного рецензента не является gold, '
                           'консенсусом или разрешением менять production-пороги.'),
    }
    result['submission_payload_sha256'] = hybrid.digest(result)
    return result


def verify_validated_submission(packet: dict, submission: dict) -> dict:
    packet = _verified_packet(packet)
    value = copy.deepcopy(submission)
    expected = value.pop('submission_payload_sha256', None)
    if (not expected or hybrid.digest(value) != expected
            or value.get('version') != SUBMISSION_VERSION + '-validated'
            or value.get('package_id') != packet['package_id']
            or value.get('packet_payload_sha256') != packet['packet_payload_sha256']
            or value.get('complete') is not True
            or value.get('individual_opinion_not_gold') is not True
            or value.get('calibration_eligible') is not False):
        raise ValueError('Проверенная анкета повреждена или относится к другому пакету.')
    value['submission_payload_sha256'] = expected
    return value


def _compare_validated(packet: dict, a: dict, b: dict, policy: dict) -> dict:
    if not a['complete'] or not b['complete']:
        raise ValueError('Для сравнения нужны две полные анкеты.')
    if a['reviewer_id'].casefold() == b['reviewer_id'].casefold():
        raise ValueError('Нужны два разных рецензента.')
    by_a = {row['item_id']: row for row in a['annotations']}
    by_b = {row['item_id']: row for row in b['annotations']}
    axes, disagreements = {}, []
    for name in policy['questions']:
        pairs = Counter((by_a[item]['answers'][name], by_b[item]['answers'][name])
                        for item in sorted(by_a))
        agreed = sum(count for (x, y), count in pairs.items() if x == y)
        axes[name] = {'items': len(by_a), 'agreements': agreed,
                      'raw_agreement': agreed / len(by_a),
                      'answer_pairs': {f'{x} | {y}': count
                                       for (x, y), count in sorted(pairs.items())}}
        disagreements.extend({'item_id': item, 'axis': name,
                              'left': by_a[item]['answers'][name],
                              'right': by_b[item]['answers'][name]}
                             for item in sorted(by_a)
                             if by_a[item]['answers'][name] != by_b[item]['answers'][name])
    result = {
        'version': COMPARISON_VERSION,
        'package_id': packet['package_id'],
        'packet_payload_sha256': packet['packet_payload_sha256'],
        'reviewers': [a['reviewer_id'], b['reviewer_id']],
        'submission_sha256': [a['submission_payload_sha256'], b['submission_payload_sha256']],
        'axes': axes,
        'disagreements': disagreements,
        'consensus_created': False,
        'adjudication_required': bool(disagreements),
        'calibration_allowed': False,
        'interpretation': ('Сырая согласованность описательна. Разногласия требуют отдельного '
                           'решения; сравнение не создаёт gold автоматически.'),
    }
    result['comparison_payload_sha256'] = hybrid.digest(result)
    return result


def compare_submissions(packet: dict, left: dict, right: dict,
                        policy: dict | None = None) -> dict:
    packet = _verified_packet(packet)
    policy = policy or load_policy()
    a = validate_submission(packet, left, policy)
    b = validate_submission(packet, right, policy)
    return _compare_validated(packet, a, b, policy)


def compare_validated_submissions(packet: dict, left: dict, right: dict,
                                  policy: dict | None = None) -> dict:
    packet = _verified_packet(packet)
    policy = policy or load_policy()
    validate_policy(policy)
    a = verify_validated_submission(packet, left)
    b = verify_validated_submission(packet, right)
    return _compare_validated(packet, a, b, policy)


def database_evidence(snapshot: dict) -> tuple[dict[int, dict], set[int]]:
    ids = sorted({work_id for candidate in snapshot['candidates']
                  for work_id in candidate['work_ids']})
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT w.work_id,w.canonical_title,w.effective_date,i.kind,i.value '
                    'FROM work w LEFT JOIN identifier i USING(work_id) '
                    'WHERE w.run_id=%s AND w.work_id=ANY(%s) '
                    'ORDER BY w.work_id,i.kind,i.value',
                    (snapshot['provenance']['normalize_run_id'], ids))
        evidence = {work_id: {'title': None, 'published_at': None, 'sources': [],
                              'arxiv_ids': []} for work_id in ids}
        for work_id, title, published_at, kind, value in cur.fetchall():
            row = evidence[work_id]
            row['title'], row['published_at'] = title, published_at.isoformat()
            if kind == 'arxiv':
                row['sources'].append('https://arxiv.org/abs/' + value)
                row['arxiv_ids'].append(value)
            elif kind == 'doi':
                row['sources'].append('https://doi.org/' + value)
            elif kind == 'openalex':
                row['sources'].append(value if value.startswith('https://')
                                      else 'https://openalex.org/' + value)
    if any(not row['title'] or not row['published_at'] for row in evidence.values()):
        raise ValueError('Не найдены обязательные библиографические метаданные работ.')
    catalog = load_catalog()
    control_ids = {ref for case in catalog['cases'] for ref in case['arxiv_ids']}
    excluded = {work_id for work_id, row in evidence.items()
                if set(row['arxiv_ids']) & control_ids}
    return evidence, excluded


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def _write_new(path: Path, payload: dict) -> None:
    with path.open('x') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description='Ослеплённый экспертный пакет без готовых меток')
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('snapshot_id')
    build.add_argument('--packet', type=Path, required=True)
    build.add_argument('--private-key', type=Path, required=True)
    build.add_argument('--template', type=Path, required=True)
    build.add_argument('--max-items', type=int)
    build.add_argument('--seed')
    check = sub.add_parser('validate')
    check.add_argument('--packet', type=Path, required=True)
    check.add_argument('--submission', type=Path, required=True)
    check.add_argument('--output', type=Path, required=True)
    compare = sub.add_parser('compare')
    compare.add_argument('--packet', type=Path, required=True)
    compare.add_argument('--left', type=Path, required=True)
    compare.add_argument('--right', type=Path, required=True)
    compare.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        snapshot = hybrid.read(args.snapshot_id)
        evidence, excluded = database_evidence(snapshot)
        portfolio = project(snapshot)
        packet, key, template = build_packet(snapshot, portfolio['candidates'], evidence,
                                             excluded, max_items=args.max_items, seed=args.seed)
        for path, payload in ((args.packet, packet), (args.private_key, key),
                              (args.template, template)):
            _write_new(path, payload)
        print(json.dumps({'package_id': packet['package_id'],
                          'packet_payload_sha256': packet['packet_payload_sha256'],
                          'selected_items': len(packet['items']),
                          'excluded_known_control_candidates':
                              packet['selection_summary']['excluded_known_control_candidates']},
                         ensure_ascii=False))
    elif args.command == 'validate':
        result = validate_submission(_read(args.packet), _read(args.submission))
        _write_new(args.output, result)
        print(json.dumps({'complete': result['complete'],
                          'submission_payload_sha256': result['submission_payload_sha256']},
                         ensure_ascii=False))
    else:
        result = compare_submissions(_read(args.packet), _read(args.left), _read(args.right))
        _write_new(args.output, result)
        print(json.dumps({'disagreements': len(result['disagreements']),
                          'comparison_payload_sha256': result['comparison_payload_sha256']},
                         ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
