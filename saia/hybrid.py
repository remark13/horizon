"""Гибрид предложений: семантические линии плюс редкие буквальные фразы.

Только точное равенство составов объединяет кандидатов. Пересечение не
порождает гигантский кластер и не является доказательством синонимии.
Контрольные названия и статьи поступают лишь в отдельный evaluator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from saia import db, runs, terminology
from saia.cluster import contiguous_windows, window_of, window_bounds
from saia.measurement import WindowCounts, analyze_series

POLICY_PATH = Path(__file__).resolve().parents[1] / 'config' / 'hybrid.v0.4.yaml'


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def lexical_policy(cfg: dict) -> dict:
    filename = cfg.get('lexical_policy_file', terminology.POLICY_PATH.name)
    if Path(filename).name != filename or not filename.endswith('.yaml'):
        raise ValueError('Паспорт фраз должен быть именем YAML в config.')
    p = yaml.safe_load(terminology.POLICY_PATH.with_name(filename).read_text())
    p.update(version=cfg['version'] + '+rare-lexical', selection='rare_recent_title',
             max_candidates=cfg['max_lexical_candidates'],
             max_document_support=cfg['max_document_support'],
             recent_birth_windows=cfg['recent_birth_windows'],
             min_title_support=cfg['min_title_support'],
             min_document_support=cfg['min_document_support'])
    p['stopwords'] += ['many', 'several', 'some', 'various', 'other']
    return p


def fuse(lexical: list[dict], semantic: list[dict], eligible_ids: set[int],
         max_links: int = 3) -> list[dict]:
    if max_links < 1:
        raise ValueError('Нужен положительный лимит связей.')
    grouped = {}
    for channel, items in (('lexical', lexical), ('semantic', semantic)):
        for item in items:
            ids = frozenset(item['work_ids'])
            if not ids or len(ids) != len(item['work_ids']) or not ids <= eligible_ids:
                raise ValueError('Состав кандидата пуст, повторён или выходит за допущенный корпус.')
            candidate_id = 'hybrid:' + digest(sorted(ids))[:20]
            candidate = grouped.setdefault(ids, {
                'candidate_id': candidate_id, 'work_ids': sorted(ids),
                'channels': [], 'aliases': [], 'semantic_topic_ids': [],
                'contexts': [], 'status': 'unassessed_candidate', 'emergence_score': None,
            })
            if channel not in candidate['channels']:
                candidate['channels'].append(channel)
            if channel == 'lexical':
                candidate['aliases'].append(item['phrase'])
                candidate['contexts'].extend(item['contexts'])
            else:
                candidate['semantic_topic_ids'].append(item['topic_id'])
                candidate.setdefault('semantic_labels', []).append(item['label'])
    result = list(grouped.values())
    for c in result:
        c['channels'].sort()
        c['aliases'] = sorted(set(c['aliases']), key=lambda p: (-len(p.split()), -len(p), p))
        c['semantic_topic_ids'].sort()
        c['label'] = c['aliases'][0] if c['aliases'] else ' / '.join(c['semantic_labels'])
        contexts = {(v['work_id'], v['field'], v['start'], v['end']): v for v in c['contexts']}
        c['contexts'] = [contexts[k] for k in sorted(contexts)]
        c['overlap_links'] = []
    # Связь строится между независимыми составами, не транзитивно.
    semantic_candidates = [c for c in result if 'semantic' in c['channels']]
    for c in result:
        if 'lexical' not in c['channels']:
            continue
        ids = set(c['work_ids'])
        links = []
        for s in semantic_candidates:
            shared = ids.intersection(s['work_ids'])
            if s is c or not shared:
                continue
            links.append({'candidate_id': s['candidate_id'], 'shared_work_ids': sorted(shared),
                          'lexical_fraction': len(shared) / len(ids),
                          'semantic_fraction': len(shared) / len(s['work_ids']),
                          'jaccard': len(shared) / len(ids.union(s['work_ids'])),
                          'interpretation': 'Пересечение работ, не объединение смыслов или статусов.'})
        c['overlap_links'] = sorted(links, key=lambda v: (-len(v['shared_work_ids']),
                                                        -v['jaccard'], v['candidate_id']))[:max_links]
    return sorted(result, key=lambda c: c['candidate_id'])


def add_series(candidates: list[dict], docs: list[terminology.PublicationText],
               as_of: date, start: date | None, end: date, step: str,
               comparable: bool) -> None:
    if step not in ('year', 'quarter'):
        raise ValueError('Поддерживаются только годовые и квартальные окна.')
    if not docs:
        if candidates:
            raise ValueError('Пустой корпус не может иметь кандидатов.')
        return
    first = start or min(d.published_at for d in docs)
    # Этот эксперимент не считает неполное первое окно полным наблюдением.
    if start and window_bounds(window_of(start, step), step)[0] != start:
        raise ValueError('Для гибридного сравнения начало периода должно совпадать с началом окна.')
    grid = contiguous_windows(window_of(first, step), window_of(end - timedelta(days=1), step), step)
    corpus = Counter(window_of(d.published_at, step) for d in docs)
    by_id = {d.work_id: d for d in docs}
    for c in candidates:
        counts = Counter(window_of(by_id[i].published_at, step) for i in c['work_ids'])
        windows = []
        for key in grid:
            a, inclusive_b = window_bounds(key, step)
            b = inclusive_b + timedelta(days=1)
            windows.append(WindowCounts(a, min(b, end), counts[key], corpus[key], b <= end, comparable))
        c['publication_series'] = analyze_series(windows, as_of)
        c['first_observed_in_corpus'] = min(by_id[i].published_at for i in c['work_ids']).isoformat()
        c['document_support'] = len(c['work_ids'])
        c['unknown_checks'] = ['Смысловая связность нового состава не откалибрована.',
                               'Независимость групп нового состава не проверена.',
                               'Новизна вне сохранённого корпуса не установлена.']


def recovery(candidates: list[dict], target_ids: set[int]) -> dict:
    """Post-hoc полнота восстановления; не истинность сигнала или precision."""
    if not target_ids:
        return {'eligible_targets': 0, 'dominant_target_recall': None,
                'dominant_target_share': None, 'max_targets_one_candidate': None}
    ranked = sorted(candidates, key=lambda c: (-len(target_ids.intersection(c['work_ids'])),
                                              len(c['work_ids']), c['candidate_id']))
    best = ranked[0] if ranked else None
    n = len(target_ids.intersection(best['work_ids'])) if best else 0
    # A zero-overlap candidate is not a recovered topic, even if sorting picked it.
    if n == 0:
        best = None
    return {'eligible_targets': len(target_ids), 'max_targets_one_candidate': n,
            'dominant_target_recall': n / len(target_ids),
            'dominant_target_share': n / len(best['work_ids']) if best else None,
            'dominant_candidate_id': best['candidate_id'] if best else None,
            'dominant_label': best['label'] if best else None,
            'interpretation': 'Восстановление состава контрольной темы, не выявление слабого сигнала.'}


def analyze(mission_id: str, cluster_run_id: int, policy_path: Path | None = None) -> dict:
    cfg = yaml.safe_load((policy_path or POLICY_PATH).read_text())
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT upstream_run_id, notes, window_step, embedding_model, as_of_date, "
                    "query_version_id, code_version FROM analysis_run WHERE run_id = %s "
                    "AND mission_id = %s AND kind = 'cluster' AND status = 'done'",
                    (cluster_run_id, mission_id))
        row = cur.fetchone()
        if not row:
            raise ValueError('Нужен завершённый семантический прогон этой миссии.')
        root, notes, step, model, cluster_as_of, query_id, cluster_code = row
        generation = (notes or {}).get('quality_generation_id')
        if not generation:
            raise ValueError('У семантического прогона не закреплено поколение качества.')
        docs, as_of, start, end, comparable, provenance = terminology.read_corpus(cur, mission_id, generation)
        if root != provenance['normalize_run_id'] or as_of != cluster_as_of or query_id != provenance['query_version_id']:
            raise ValueError('Семантический и текстовый входы не совпали.')
        eligible = {d.work_id for d in docs}
        cur.execute('SELECT t.topic_id, t.label, tm.work_id FROM topic t '
                    'LEFT JOIN topic_membership tm USING (topic_id) WHERE t.run_id = %s '
                    'ORDER BY t.topic_id, tm.work_id', (cluster_run_id,))
        members = defaultdict(set)
        labels = {}
        for topic, label, work in cur.fetchall():
            labels[topic] = label or f'Тема #{topic}'
            if work is not None:
                members[topic].add(work)
        semantic = [{'topic_id': i, 'label': labels[i], 'work_ids': sorted(ids)}
                    for i, ids in members.items()]
        if any(not set(s['work_ids']) <= eligible for s in semantic):
            raise ValueError('В семантическом составе есть будущая или недопущенная работа.')
        cur.execute('SELECT e.work_id FROM work_embedding e JOIN work w USING (work_id) '
                    'WHERE w.run_id = %s AND e.model = %s', (root, model))
        vector_ids = {r[0] for r in cur.fetchall()} & eligible
        missing_vectors = sorted(eligible - vector_ids)
        if any(not set(s['work_ids']) <= vector_ids for s in semantic):
            raise ValueError('Семантические назначения не подтверждены сохранёнными векторами.')
        lexical = terminology.generate(docs, as_of, step, comparable, lexical_policy(cfg), start, end)
        terminology.attach_context_sources(cur, lexical)
        candidates = fuse(lexical['candidates'], semantic, eligible, cfg['max_overlap_links'])
        add_series(candidates, docs, as_of, start, end, step, comparable)
        result = {
            'version': cfg['version'], 'effective_policy': cfg, 'policy_hash': digest(cfg),
            'provenance': {**provenance, 'cluster_run_id': cluster_run_id,
                           'cluster_code_version': cluster_code, 'embedding_model': model},
            'as_of_date': as_of.isoformat(), 'period_from': start.isoformat() if start else None,
            'period_end_exclusive': end.isoformat(), 'window_step': step,
            'runtime': runs.runtime_snapshot(), 'input_text_hash': lexical['input_text_hash'],
            'lexical_policy_hash': lexical['policy_hash'],
            'effective_lexical_policy': lexical['effective_policy'],
            'eligible_work_ids': sorted(eligible), 'missing_vector_work_ids': missing_vectors,
            'semantic_membership_hash': digest(semantic),
            'counts': {'eligible_corpus': len(docs), 'semantic_input_vectors': len(vector_ids),
                       'semantic_lines': len(semantic), 'empty_semantic_lines': len(labels) - len(semantic),
                       'lexical_phrases_before_cap': lexical['selection_eligible_phrases'],
                       'lexical_phrases_selected': len(lexical['candidates']),
                       'hybrid_candidates': len(candidates),
                       'lexical_only_candidates': sum(c['channels'] == ['lexical'] for c in candidates),
                       'both_channel_candidates': sum(len(c['channels']) == 2 for c in candidates)},
            'candidates': candidates,
            'limitations': [cfg['interpretation'],
                            'Отбор редких недавних фраз экспериментален; малый размер сам по себе не новизна.',
                            'Первое наблюдение относится только к загруженному корпусу.',
                            'Кандидаты не получили forming или emergence score; прежний score не перенесён.',
                            'Современная модель и современные метаданные ограничивают историческую интерпретацию.',
                            'Отсутствующие векторы и непроверенное покрытие не скрываются.'],
        }
        snapshot_id = str(uuid.uuid4())
        result['snapshot_id'] = snapshot_id
        # Публичный результат совпадает с повторным чтением JSONB (tuple → list).
        result = json.loads(json.dumps(result, ensure_ascii=False))
        cur.execute('INSERT INTO hybrid_snapshot (snapshot_id, mission_id, normalize_run_id, '
                    'quality_generation_id, cluster_run_id, payload, content_sha256) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                    (snapshot_id, mission_id, root, generation, cluster_run_id, Jsonb(result), digest(result)))
    return result


def read(snapshot_id: str) -> dict:
    try:
        uuid.UUID(snapshot_id)
    except (ValueError, TypeError):
        raise ValueError('Некорректный номер гибридного снимка.') from None
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT payload, content_sha256 FROM hybrid_snapshot WHERE snapshot_id = %s', (snapshot_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError('Гибридный снимок не найден.')
    if digest(row[0]) != row[1]:
        raise ValueError('Отпечаток гибридного снимка не совпал.')
    return row[0]


def history(mission_id: str) -> dict:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT snapshot_id, cluster_run_id, quality_generation_id, created_at, '
                    "payload->>'version', payload->'counts' FROM hybrid_snapshot "
                    'WHERE mission_id = %s ORDER BY created_at DESC, snapshot_id DESC LIMIT 20', (mission_id,))
        return {'mission_id': mission_id, 'snapshots': [
            {'snapshot_id': str(i), 'cluster_run_id': c, 'quality_generation_id': q,
             'created_at': t.isoformat(), 'version': v, 'counts': counts}
            for i, c, q, t, v, counts in cur.fetchall()]}


def compare(left: dict, right: dict) -> dict:
    keys = ['version', 'policy_hash', 'lexical_policy_hash', 'input_text_hash',
            'semantic_membership_hash', 'runtime', 'as_of_date', 'period_from',
            'period_end_exclusive', 'window_step', 'missing_vector_work_ids']
    same_inputs = all(left[k] == right[k] for k in keys) and left['provenance'] == right['provenance']
    signatures = [digest(p['candidates']) for p in (left, right)]
    return {'left_snapshot_id': left['snapshot_id'], 'right_snapshot_id': right['snapshot_id'],
            'same_inputs_code_and_runtime': same_inputs, 'candidate_signatures': signatures,
            'repeatability_passed': signatures[0] == signatures[1] if same_inputs else None,
            'scope': 'Повтор гибридного генератора на том же завершённом семантическом входе, не всего конвейера.'}


def evaluate(result: dict, target_arxiv_ids: list[str]) -> dict:
    # Генерация уже завершена; названия/ID контроля не поступают в analyze.
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT i.value, i.work_id FROM identifier i JOIN work w USING (work_id) "
                    "WHERE w.run_id = %s AND i.kind = 'arxiv' AND i.value = ANY(%s)",
                    (result['provenance']['normalize_run_id'], target_arxiv_ids))
        mapped = {v: w for v, w in cur.fetchall()}
    ids = set(mapped.values()) & set(result['eligible_work_ids'])
    views = {
        'lexical': [c for c in result['candidates'] if 'lexical' in c['channels']],
        'semantic': [c for c in result['candidates'] if 'semantic' in c['channels']],
        'hybrid': result['candidates'],
    }
    return {'snapshot_id': result['snapshot_id'], 'target_arxiv_ids': target_arxiv_ids,
            'eligible_target_work_ids': sorted(ids),
            'unavailable_or_ineligible_target_ids': sorted(set(target_arxiv_ids) - {v for v, w in mapped.items() if w in ids}),
            'channels': {k: recovery(v, ids) for k, v in views.items()},
            'weak_signal_detection': 'not_evaluated', 'heldout': False,
            'interpretation': 'Post-hoc проверка восстановления известной разработчику темы; не gold-оценка детектора.'}


def main() -> int:
    parser = argparse.ArgumentParser(description='Гибрид кандидатов, не подтверждённые слабые сигналы')
    parser.add_argument('mission_id', nargs='?')
    parser.add_argument('--cluster-run', type=int)
    parser.add_argument('--policy', type=Path, help='Явная версия паспорта гибрида')
    parser.add_argument('--snapshot')
    parser.add_argument('--export', required=True)
    parser.add_argument('--evaluate-mission', help='JSON миссии с benchmark, используется только после генерации')
    parser.add_argument('--compare-snapshot', help='Сохранённый снимок для проверки повторяемости генератора')
    args = parser.parse_args()
    if args.snapshot:
        if args.policy:
            parser.error('При чтении сохранённого снимка нельзя подменять паспорт.')
        result = read(args.snapshot)
    elif args.mission_id and args.cluster_run:
        result = analyze(args.mission_id, args.cluster_run, args.policy)
    else:
        parser.error('Нужны mission_id и --cluster-run или --snapshot.')
    Path(args.export).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result['counts'])
    if args.evaluate_mission:
        spec = json.loads(Path(args.evaluate_mission).read_text())
        if spec['mission_id'] != result['provenance']['mission_id']:
            parser.error('Контроль принадлежит другой миссии.')
        report = evaluate(result, spec['benchmark']['target_arxiv_ids'])
        path = Path(args.export).with_suffix('.evaluation.json')
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(report['channels'])
    if args.compare_snapshot:
        report = compare(read(args.compare_snapshot), result)
        Path(args.export).with_suffix('.repeatability.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
